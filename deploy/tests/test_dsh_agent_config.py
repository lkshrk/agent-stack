"""Check the dsh prompts, team skills and model choices against the contract of h-cloud's upsert-patch.mjs and dsh-superagent."""

from pathlib import Path
import re
import unittest

import yaml

DSH = Path(__file__).resolve().parents[1] / "dsh"
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
SECRET_KEYS = re.compile(r"(?i)secret|token|password|api.?key")


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
        for name in ("models.yaml", "superagent.yaml"):
            with self.subTest(file=name):
                _, documents = load_entries(name)
                self.assertEqual(len(documents), 1)
                entries = documents[0]
                self.assertIsInstance(entries, list)
                for entry in entries:
                    self.assertIsInstance(entry["id"], str)
                    self.assertNotIn("delete", entry)
                    if "config" in entry:
                        self.assertNotIn("path", entry)
                    else:
                        self.assertIsInstance(entry["path"], list)
                        self.assertTrue(entry["path"])
                        self.assertIn("value", entry)

    def test_no_secret_looking_values(self):
        for name in ("models.yaml", "superagent.yaml"):
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


if __name__ == "__main__":
    unittest.main()
