import copy
import os
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
INSTALLED = Path("/opt/agent-stack/hermes-profiles")
USEFUL_SKILLS = Path("/opt/useful-skills/skills")
HERMES_SKILLS = ["upgrade-flux-cluster"]

PER_PROFILE_KEYS = ("model", "agent", "delegation", "mcp_servers", "plugins", "platform_toolsets", "kanban", "gateway")


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


def default() -> dict:
    return load(PROFILES / "default" / "config.yaml")


def effective() -> dict:
    return deep_merge(copy.deepcopy(default()), managed())


class ConfigTest(unittest.TestCase):
    def setUp(self):
        if yaml is None:
            self.skipTest("needs PyYAML or hermes_yaml")

    def test_only_the_default_profile_ships(self):
        self.assertEqual(sorted(p.name for p in PROFILES.iterdir()), ["default"])

    def test_one_gateway_without_kanban_dispatch(self):
        cfg = effective()
        self.assertNotIn("multiplex_profiles", cfg.get("gateway") or {})
        self.assertEqual(cfg["kanban"], {"dispatch_in_gateway": False})

    def test_managed_has_no_per_profile_keys(self):
        cfg = managed()
        for key in PER_PROFILE_KEYS:
            self.assertNotIn(key, cfg, key)

    def test_reads_only_the_chosen_useful_skills(self):
        self.assertEqual(default()["skills"]["external_dirs"], [str(USEFUL_SKILLS / s) for s in HERMES_SKILLS])
        self.assertFalse((PROFILES / "default" / "skills").exists())

    @unittest.skipUnless((REPO / "images").is_dir(), "needs the repository checkout")
    def test_useful_skills_pin_matches_in_both_images(self):
        pins = {(REPO / "images" / i / "Dockerfile").read_text().split("ARG USEFUL_SKILLS_VERSION=")[1].split()[0]
                for i in ("hermes", "hermes-webui")}
        self.assertEqual(len(pins), 1, pins)
        pin = pins.pop()
        self.assertRegex(pin, r"^v\d+\.\d+\.\d+@[0-9a-f]{40}$")
        self.assertNotRegex(pin, r"@0{40}$")

    def test_mcp_secrets_are_env_references(self):
        for server, spec in (default().get("mcp_servers") or {}).items():
            for header, value in (spec.get("headers") or {}).items():
                self.assertRegex(value, r"^\$\{[A-Z][A-Z0-9_]*\}$", f"{server}:{header}")


@unittest.skipUnless(IN_IMAGE, "needs the Hermes image (AGENT_STACK_IN_IMAGE=1)")
class ConfigInImageTest(unittest.TestCase):
    def test_installed_profile_assets(self):
        shipped = PROFILES / "default"
        for asset in shipped.rglob("*"):
            if asset.is_file():
                self.assertEqual((INSTALLED / "default" / asset.relative_to(shipped)).read_bytes(), asset.read_bytes())
        self.assertEqual(sorted(p.name for p in INSTALLED.iterdir()), ["default"])

    def test_installed_assets_not_writable_by_hermes(self):
        for path in [INSTALLED, *INSTALLED.rglob("*")]:
            mode = path.lstat().st_mode
            self.assertEqual(path.lstat().st_uid, 0, path)
            self.assertFalse(mode & 0o022, path)

    def test_managed_config_installed(self):
        self.assertEqual(Path("/etc/hermes/config.yaml").read_bytes(), (CONFIG / "config.yaml").read_bytes())

    def test_merge_matches_hermes(self):
        from hermes_cli.config import _deep_merge

        self.assertEqual(_deep_merge(copy.deepcopy(default()), managed()), effective())

    def test_useful_skills_installed_read_only(self):
        for skill in HERMES_SKILLS:
            text = (USEFUL_SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith(f"---\nname: {skill}\n"), skill)
        self.assertFalse(Path("/opt/useful-skills/.git").exists())
        for path in [USEFUL_SKILLS, *USEFUL_SKILLS.rglob("*")]:
            self.assertFalse(path.lstat().st_mode & 0o222 and not path.is_symlink(), path)

    def test_shipped_skill_resolves(self):
        import json
        import tempfile

        from agent import skill_utils
        from tools.skills_tool import skill_view

        saved = os.environ.get("HERMES_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config.yaml").write_text(yaml.safe_dump({"skills": default()["skills"]}))
            os.environ["HERMES_HOME"] = tmp
            try:
                skill_utils._external_dirs_cache_clear()
                shipped = json.loads(skill_view("upgrade-flux-cluster"))
                self.assertTrue(shipped.get("success"), shipped)
                self.assertIn("## Repository specifics", shipped["content"])
                hidden = json.loads(skill_view("wow-help"))
                self.assertFalse(hidden.get("success"), hidden)
            finally:
                skill_utils._external_dirs_cache_clear()
                if saved is None:
                    os.environ.pop("HERMES_HOME", None)
                else:
                    os.environ["HERMES_HOME"] = saved
