import copy
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_stack_policies.public_contributions import Registry, RegistryError

REGISTRY_PATH = Path(__file__).resolve().parents[3] / "config" / "public-contributions.yaml"


class PublicContributionsTest(unittest.TestCase):
    def load(self, registry):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.yaml"
            path.write_text(yaml.safe_dump(registry, sort_keys=False))
            return Registry.load(path)

    def registry(self):
        return yaml.safe_load(REGISTRY_PATH.read_text())

    def test_loads_omnigent(self):
        registry = Registry.load(REGISTRY_PATH)
        repository = registry.repository("omnigent")

        self.assertEqual(repository.upstream, "https://github.com/omnigent-ai/omnigent.git")
        self.assertEqual(repository.checkout, "omnigent")
        self.assertEqual(repository.validation.name, "web")
        self.assertEqual(
            [command.name for command in repository.validation_profile("web").commands],
            ["lint", "typecheck", "test", "build"],
        )
        self.assertEqual(len(registry.revision), 64)

    def test_rejects_unknown_field(self):
        registry = self.registry()
        registry["repositories"]["omnigent"]["unexpected"] = True

        with self.assertRaisesRegex(RegistryError, "unknown field"):
            self.load(registry)

    def test_rejects_duplicate_normalized_checkout(self):
        registry = self.registry()
        duplicate = copy.deepcopy(registry["repositories"]["omnigent"])
        duplicate["checkout"] = "omnigent"
        registry["repositories"]["duplicate"] = duplicate

        with self.assertRaisesRegex(RegistryError, "duplicate checkout"):
            self.load(registry)

    def test_rejects_parent_checkout(self):
        registry = self.registry()
        registry["repositories"]["omnigent"]["checkout"] = "../omnigent"

        with self.assertRaisesRegex(RegistryError, "checkout"):
            self.load(registry)

    def test_rejects_non_public_or_non_https_upstream(self):
        for upstream, visibility in [
            ("http://github.com/omnigent-ai/omnigent.git", "public"),
            ("https://github.com/omnigent-ai/omnigent.git", "private"),
        ]:
            with self.subTest(upstream=upstream, visibility=visibility):
                registry = self.registry()
                entry = registry["repositories"]["omnigent"]
                entry["upstream"] = upstream
                entry["visibility"] = visibility

                with self.assertRaisesRegex(RegistryError, "public HTTPS GitHub"):
                    self.load(registry)

    def test_rejects_github_url_alias(self):
        registry = self.registry()
        registry["repositories"]["omnigent"]["upstream"] = "https://github.com/omnigent-ai/omnigent"

        with self.assertRaisesRegex(RegistryError, "canonical GitHub"):
            self.load(registry)

    def test_rejects_unpinned_validator_image(self):
        registry = self.registry()
        registry["repositories"]["omnigent"]["validation"]["image"] = "ghcr.io/lkshrk/agent-stack/public-validator:latest"

        with self.assertRaisesRegex(RegistryError, "pinned digest"):
            self.load(registry)

    def test_rejects_duplicate_profile_command_name(self):
        registry = self.registry()
        commands = registry["repositories"]["omnigent"]["validation"]["commands"]
        commands.append(copy.deepcopy(commands[0]))

        with self.assertRaisesRegex(RegistryError, "duplicate command"):
            self.load(registry)

    def test_rejects_shell_string_command(self):
        registry = self.registry()
        registry["repositories"]["omnigent"]["validation"]["commands"][0]["argv"] = "pnpm run lint"

        with self.assertRaisesRegex(RegistryError, "argv"):
            self.load(registry)


if __name__ == "__main__":
    unittest.main()
