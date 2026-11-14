"""Check the dsh agent config against the contract of h-cloud's upsert-patch.mjs, upsert-mcp.mjs and dsh-superagent."""

from pathlib import Path
import re
import unittest

import yaml

DSH = Path(__file__).resolve().parents[1] / "dsh"
LITELLM = Path(__file__).resolve().parents[1] / "litellm"
ROW_FILES = ("models.yaml", "openviking-row.yaml", "superagent.yaml")
MCP_ROUTE = re.compile(r"^http://litellm-proxy\.ai\.svc\.cluster\.local:4000/mcp/([A-Za-z0-9_-]+)$")
SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
MCP_KEYS = {"serverName", "url", "enabled", "offByDefault"}
PROVIDER = "litellm"
MODEL_ID = re.compile(r"\b(?:anthropic|openai|moonshot)/[a-z0-9][a-z0-9.-]*[a-z0-9]")
TEAM_SECTIONS = {"communication", "scope", "delivery"}
SKILL_KEY = re.compile(r"^[a-z][a-z0-9-]*$")
BUILT_IN_SKILLS = {"feishu", "github"}
# Same pattern as dsh-superagent's teamSkill(): name, then description, then the body.
FRONTMATTER = re.compile(r"^---\nname: ([^\n]+)\ndescription: ([^\n]+)\n---\n+([\s\S]*)$")
SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_|lin_(?:api|oauth)_|xox[abp]-|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY|eyJ[A-Za-z0-9_-]{10,}\.|[A-Za-z0-9+/]{40,}={0,2}"
)
SECRET_KEYS = re.compile(r"(?i)secret|(?<!max)token|password|api.?key|bearer")


def load_entries(name):
    text = (DSH / name).read_text()
    documents = list(yaml.safe_load_all(text))
    return text, documents


def strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from strings(item)


def keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from keys(item)


class UpsertContractTest(unittest.TestCase):
    def test_files_are_one_document_with_valid_entries(self):
        for name in ROW_FILES:
            with self.subTest(file=name):
                _, documents = load_entries(name)
                self.assertEqual(len(documents), 1)
                entries = documents[0]
                self.assertIsInstance(entries, list)
                for entry in entries:
                    self.assertIsInstance(entry["id"], str)
                    self.assertNotIn("delete", entry)
                    if set(entry) == {"id", "disabled"}:
                        self.assertIsInstance(entry["disabled"], bool)
                    elif "config" in entry:
                        self.assertNotIn("path", entry)
                    else:
                        self.assertIsInstance(entry["path"], list)
                        self.assertTrue(entry["path"])
                        self.assertIn("value", entry)

    def test_no_secret_looking_values(self):
        for name in (*ROW_FILES, "mcp-servers.yaml"):
            text, documents = load_entries(name)
            with self.subTest(file=name):
                self.assertIsNone(SECRET.search(text))
                self.assertEqual([k for k in keys(documents) if SECRET_KEYS.search(k)], [])


class ModelsTest(unittest.TestCase):
    def setUp(self):
        self.rows = {entry["id"]: entry for entry in load_entries("models.yaml")[1][0]}

    def test_only_agent_model_rows(self):
        self.assertEqual(set(self.rows), {"agent-default-model", "subagent-model-selection-settings"})

    def test_default_model_is_allowed(self):
        allowed = self.rows["subagent-model-selection-settings"]["config"]["allowedModels"]
        self.assertTrue(self.rows["subagent-model-selection-settings"]["config"]["enabled"])
        self.assertIn(self.rows["agent-default-model"]["config"], allowed)
        for model in allowed:
            with self.subTest(model=model):
                self.assertEqual(model["provider"], PROVIDER)
                self.assertRegex(model["model"], MODEL_ID)


class SuperagentTest(unittest.TestCase):
    def setUp(self):
        self.entries = load_entries("superagent.yaml")[1][0]
        self.by_path = {tuple(entry["path"]): entry["value"] for entry in self.entries}
        allowed = {entry["id"]: entry for entry in load_entries("models.yaml")[1][0]}
        self.allowed = allowed["subagent-model-selection-settings"]["config"]["allowedModels"]

    def test_only_agent_keys_of_the_managed_block(self):
        self.assertEqual({entry["id"] for entry in self.entries}, {"dsh-superagent"})
        self.assertEqual(set(self.by_path), {("managed", "executor", "model"), ("managed", "executor", "modelRules"), ("managed", "prompts")})

    def test_executor_models_are_allowed(self):
        self.assertIn(self.by_path["managed", "executor", "model"], self.allowed)
        for rule in self.by_path["managed", "executor", "modelRules"]:
            with self.subTest(rule=rule["label"]):
                self.assertIn(rule["model"], self.allowed)

    def test_prompt_sections(self):
        prompts = self.by_path["managed", "prompts"]
        self.assertLessEqual(set(prompts) - {"skills"}, TEAM_SECTIONS)
        for section in TEAM_SECTIONS & set(prompts):
            value = prompts[section]
            with self.subTest(section=section):
                text = value if isinstance(value, str) else value["append"]
                self.assertTrue(text.strip())

    def test_skill_frontmatter_names_match_their_keys(self):
        skills = self.by_path["managed", "prompts"]["skills"]
        self.assertTrue(skills)
        for key, text in skills.items():
            with self.subTest(skill=key):
                self.assertRegex(key, SKILL_KEY)
                self.assertNotIn(key, BUILT_IN_SKILLS)
                match = FRONTMATTER.match(text)
                self.assertIsNotNone(match)
                self.assertEqual(match[1].strip(), f"dsh-superagent-{key}")
                self.assertTrue(match[2].strip())
                self.assertTrue(match[3].strip())

    def test_loaded_skills_exist(self):
        prompts = self.by_path["managed", "prompts"]
        named = {f"dsh-superagent-{key}" for key in prompts["skills"]}
        for text in strings(prompts):
            for skill in re.findall(r"load the skill (dsh-superagent-[a-z0-9-]+)", text):
                with self.subTest(skill=skill):
                    self.assertIn(skill, named)

    def test_models_named_in_prompts_are_allowed(self):
        allowed = {model["model"] for model in self.allowed}
        for text in strings(self.by_path["managed", "prompts"]):
            for model in MODEL_ID.findall(text):
                with self.subTest(model=model):
                    self.assertIn(model, allowed)


class SecretKeysTest(unittest.TestCase):
    def test_pattern_flags_credential_keys_only(self):
        for key in ("token", "tokens", "refreshTokens", "accessTokens", "authToken", "bearer", "bearerAuth", "apiKey", "password"):
            with self.subTest(key=key):
                self.assertIsNotNone(SECRET_KEYS.search(key))
        for key in ("maxTokens", "recallMaxTokens"):
            with self.subTest(key=key):
                self.assertIsNone(SECRET_KEYS.search(key))


class McpServersTest(unittest.TestCase):
    def setUp(self):
        _, documents = load_entries("mcp-servers.yaml")
        self.assertEqual(len(documents), 1)
        self.servers = documents[0]
        self.catalogue = yaml.safe_load((LITELLM / "mcp-servers.yaml").read_text())["proxy_config"]["mcp_servers"]

    def test_entries_have_only_upsert_keys(self):
        self.assertIsInstance(self.servers, list)
        self.assertTrue(self.servers)
        for server in self.servers:
            with self.subTest(server=server.get("serverName")):
                self.assertLessEqual(set(server), MCP_KEYS)
                self.assertIsInstance(server.get("enabled", True), bool)

    def test_server_names_are_valid_and_unique(self):
        names = [server["serverName"] for server in self.servers]
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            with self.subTest(server=name):
                self.assertRegex(name, SERVER_NAME)
                self.assertNotIn("__", name)
                self.assertFalse(name.endswith("_"))

    def test_urls_are_litellm_mcp_routes_of_the_catalogue(self):
        for server in self.servers:
            with self.subTest(server=server["serverName"]):
                match = MCP_ROUTE.match(server["url"])
                self.assertIsNotNone(match)
                self.assertIn(match[1], self.catalogue)


class OpenVikingRowTest(unittest.TestCase):
    def setUp(self):
        self.entries = load_entries("openviking-row.yaml")[1][0]

    def test_only_the_openviking_row(self):
        self.assertEqual({entry["id"] for entry in self.entries}, {"openviking-memory-runtime"})

    def test_row_is_enabled_with_a_config(self):
        self.assertIn({"id": "openviking-memory-runtime", "disabled": False}, self.entries)
        configs = [entry["config"] for entry in self.entries if "config" in entry]
        self.assertEqual(len(configs), 1)
        self.assertIsInstance(configs[0], dict)


if __name__ == "__main__":
    unittest.main()
