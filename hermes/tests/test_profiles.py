import copy
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:
    try:
        import hermes_yaml as yaml
    except ImportError:
        yaml = None

IN_IMAGE = os.environ.get("AGENT_STACK_IN_IMAGE") == "1"
REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "hermes"
PROFILES = CONFIG / "profiles"
MANAGED_BEFORE = Path(__file__).resolve().parent / "testdata" / "hermes" / "managed-config-v1.yaml"
INSTALLED = Path("/opt/agent-stack")
SECONDARY = ("delivery", "omnigent-runner")

H16_PER_PROFILE = {
    "model": None,
    "agent": ["max_turns", "disabled_toolsets"],
    "delegation": None,
    "memory": ["provider", "write_approval"],
    "skills": ["write_approval"],
    "approvals": None,
    "mcp_servers": None,
    "plugins": None,
    "platform_toolsets": None,
    "toolsets": None,
    "kanban": None,
    "timeouts": None,
    "multiplex_profiles": None,
    "gateway": None,
}

NO_WRITE_TOOLSETS = {"terminal", "file", "code_execution", "delegation", "skills"}
RUNNER_SOUL = (
    "Call `omnigent_run`. While it returns `call_again: true`, call it again. "
    "Do nothing else; never summarise or judge the result."
)


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def deep_merge(base: dict, override: dict) -> dict:
    """hermes_cli.config._deep_merge (v0.22.5), the managed overlay's merge."""
    result = base.copy()
    for key, value in override.items():
        over_dict = isinstance(result.get(key), dict)
        if over_dict and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        elif not (over_dict and value is None):
            result[key] = value
    return result


def managed() -> dict:
    return load(CONFIG / "config.yaml")


def profile(name: str) -> dict:
    return load(PROFILES / name / "config.yaml")


def effective(name: str) -> dict:
    return deep_merge(copy.deepcopy(profile(name)), managed())


def platform_lists(cfg: dict) -> dict:
    return cfg.get("platform_toolsets") or {}


def strip_multiplex(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    cfg.pop("multiplex_profiles", None)
    gateway = cfg.get("gateway")
    if isinstance(gateway, dict):
        gateway.pop("multiplex_profiles", None)
        if not gateway:
            cfg.pop("gateway")
    return cfg


class ProfilesTest(unittest.TestCase):
    def setUp(self):
        if yaml is None:
            self.skipTest("needs PyYAML or hermes_yaml")

    def test_delivery_write_approvals_on(self):
        cfg = effective("delivery")
        self.assertIs(cfg["skills"]["write_approval"], True)
        self.assertIs(cfg["memory"]["write_approval"], True)

    def test_delivery_has_no_skill_manage(self):
        cfg = effective("delivery")
        lists = platform_lists(cfg)
        self.assertIn("cli", lists)
        for platform, names in lists.items():
            self.assertNotIn("skills", names, platform)
            self.assertFalse([n for n in names if n.startswith("hermes-")], platform)
        self.assertIn("skills", cfg["agent"]["disabled_toolsets"])

    def test_delivery_kanban_settings(self):
        kanban = effective("delivery")["kanban"]
        self.assertIs(kanban["auto_decompose"], False)
        self.assertIs(kanban["review_dispatch"], False)
        self.assertEqual(kanban["failure_limit"], 2)

    def test_delivery_has_no_terminal_or_file_write(self):
        cfg = effective("delivery")
        for platform, names in platform_lists(cfg).items():
            self.assertFalse(NO_WRITE_TOOLSETS & set(names), platform)
        self.assertLessEqual(NO_WRITE_TOOLSETS, set(cfg["agent"]["disabled_toolsets"]))

    def test_delivery_mcp_only_linear_app(self):
        servers = effective("delivery")["mcp_servers"]
        self.assertEqual(list(servers), ["linear_app"])
        app = servers["linear_app"]
        self.assertTrue(app["url"].endswith("/mcp/linear_app"), app["url"])
        self.assertEqual(app["headers"], {"x-litellm-api-key": "${LITELLM_DELIVERY_API_KEY}"})
        for names in platform_lists(effective("delivery")).values():
            self.assertIn("linear_app", names)

    def test_delivery_is_headless(self):
        self.assertNotIn("platforms", profile("delivery"))

    def test_runner_only_toolset_is_omnigent_runner(self):
        cfg = effective("omnigent-runner")
        lists = platform_lists(cfg)
        self.assertIn("cli", lists)
        for platform, names in lists.items():
            self.assertEqual(names, ["omnigent_runner"], platform)

    def test_runner_has_no_mcp(self):
        self.assertFalse(effective("omnigent-runner").get("mcp_servers"))

    def test_runner_disables_kanban_and_write_toolsets(self):
        disabled = set(effective("omnigent-runner")["agent"]["disabled_toolsets"])
        self.assertLessEqual({"kanban", "web", "memory"} | NO_WRITE_TOOLSETS, disabled)

    def test_runner_plugin_model_and_turns(self):
        cfg = effective("omnigent-runner")
        self.assertEqual(cfg["plugins"]["enabled"], ["omnigent_runner"])
        self.assertEqual(cfg["agent"]["max_turns"], 400)
        self.assertEqual(cfg["model"], {"provider": "custom:litellm", "default": "anthropic/claude-haiku"})
        self.assertIs(cfg["auxiliary"]["background_review"]["enabled"], False)

    def test_secondary_profiles_differ_from_pa(self):
        pa = effective("default")
        for name in SECONDARY:
            cfg = effective(name)
            self.assertNotEqual(cfg.get("mcp_servers"), pa["mcp_servers"], name)
            self.assertNotEqual(cfg["agent"], pa["agent"], name)

    def test_delivery_uses_pa_model(self):
        self.assertEqual(effective("delivery")["model"], effective("default")["model"])

    def test_managed_has_no_per_profile_keys(self):
        cfg = managed()
        for key, leaves in H16_PER_PROFILE.items():
            if leaves is None:
                self.assertNotIn(key, cfg, key)
            else:
                for leaf in leaves:
                    self.assertNotIn(leaf, cfg.get(key) or {}, f"{key}.{leaf}")
        self.assertNotIn("enabled", cfg["auxiliary"]["background_review"])

    def test_pa_config_unchanged_except_multiplex(self):
        before = load(MANAGED_BEFORE)
        after = effective("default")
        self.assertEqual(strip_multiplex(after), before)

    def test_pa_multiplexes(self):
        self.assertIs(profile("default")["gateway"]["multiplex_profiles"], True)

    def test_profile_secrets_are_env_references(self):
        literal = re.compile(r"^\$\{[A-Z][A-Z0-9_]*\}$")
        for name in ("default", *SECONDARY):
            for server, spec in (profile(name).get("mcp_servers") or {}).items():
                for header, value in (spec.get("headers") or {}).items():
                    self.assertRegex(value, literal, f"{name}:{server}:{header}")

    def test_runner_soul(self):
        text = (PROFILES / "omnigent-runner" / "SOUL.md").read_text(encoding="utf-8")
        self.assertEqual(text.strip(), RUNNER_SOUL)

    def test_delivery_soul(self):
        text = (PROFILES / "delivery" / "SOUL.md").read_text(encoding="utf-8")
        self.assertIn("You never create top-level issues.", text)
        self.assertIn("You never close a card with evidence you did not get from a tool.", text)
        self.assertNotIn("Signal", text)
        self.assertEqual(len(re.findall(r"^\d\. \*\*", text, re.M)), 6)

    def test_only_secondary_profiles_have_souls(self):
        self.assertFalse((PROFILES / "default" / "SOUL.md").exists())
        for name in SECONDARY:
            self.assertTrue((PROFILES / name / "SOUL.md").is_file(), name)


@unittest.skipUnless(IN_IMAGE, "needs the Hermes image (AGENT_STACK_IN_IMAGE=1)")
class ProfilesInImageTest(unittest.TestCase):
    def test_installed_import_path(self):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        for package in ("agent_stack_hermes", "agent_stack_policies"):
            out = subprocess.run(
                [sys.executable, "-c", f"import {package}; print({package}.__file__)"],
                cwd="/", env=env, capture_output=True, text=True, check=True,
            )
            self.assertEqual(Path(out.stdout.strip()).parent, INSTALLED / package)

    def test_installed_profile_assets(self):
        root = INSTALLED / "hermes-profiles"
        self.assertEqual((root / "default" / "config.yaml").read_bytes(),
                         (PROFILES / "default" / "config.yaml").read_bytes())
        for name in SECONDARY:
            for asset in ("config.yaml", "SOUL.md"):
                self.assertEqual((root / name / asset).read_bytes(), (PROFILES / name / asset).read_bytes())
            self.assertTrue((root / name / "skills").is_dir(), name)
        plugin = root / "omnigent-runner" / "plugins" / "omnigent_runner"
        for asset in ("plugin.yaml", "__init__.py"):
            self.assertEqual((plugin / asset).read_bytes(),
                             (REPO / "hermes" / "plugins" / "omnigent_runner" / asset).read_bytes())
        self.assertFalse((root / "delivery" / "plugins").exists())

    def test_installed_assets_not_writable_by_hermes(self):
        for path in [INSTALLED, *INSTALLED.rglob("*")]:
            mode = path.lstat().st_mode
            self.assertEqual(path.lstat().st_uid, 0, path)
            self.assertFalse(mode & 0o022, path)

    def test_managed_config_installed(self):
        self.assertEqual(Path("/etc/hermes/config.yaml").read_bytes(), (CONFIG / "config.yaml").read_bytes())

    def test_merge_matches_hermes(self):
        from hermes_cli.config import _deep_merge

        for name in ("default", *SECONDARY):
            self.assertEqual(_deep_merge(copy.deepcopy(profile(name)), managed()), effective(name), name)

    def test_profile_names_valid(self):
        from hermes_cli.profiles import normalize_profile_name

        for name in SECONDARY:
            self.assertEqual(normalize_profile_name(name), name)

    def test_toolset_names_resolve(self):
        from toolsets import resolve_toolset, validate_toolset

        for name in SECONDARY:
            cfg = effective(name)
            for toolset in cfg["agent"]["disabled_toolsets"]:
                self.assertTrue(validate_toolset(toolset), f"{name}: {toolset}")
            servers = set(cfg.get("mcp_servers") or {})
            tools = set()
            for names in platform_lists(cfg).values():
                for toolset in names:
                    if toolset in servers or toolset == "omnigent_runner":
                        continue
                    self.assertTrue(validate_toolset(toolset), f"{name}: {toolset}")
                    tools |= set(resolve_toolset(toolset))
            self.assertNotIn("skill_manage", tools, name)
            self.assertFalse({"terminal", "write_file", "patch", "execute_code"} & tools, name)

    def test_init_script_creates_profiles(self):
        import pwd
        import tempfile

        hermes = pwd.getpwnam("hermes")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "profiles" / "delivery").mkdir(parents=True)
            (home / "profiles" / "delivery").chmod(0o500)
            env = {**os.environ, "HERMES_HOME": tmp}
            for _ in range(2):
                subprocess.run(["/etc/cont-init.d/04-profiles"], env=env, check=True)
            runner = (home / "profiles" / "omnigent-runner").stat()
            self.assertEqual((runner.st_uid, runner.st_gid), (hermes.pw_uid, hermes.pw_gid))
            self.assertEqual((home / "profiles" / "delivery").stat().st_mode & 0o777, 0o500)
            self.assertEqual(sorted(p.name for p in home.rglob("*")), ["delivery", "omnigent-runner", "profiles"])
