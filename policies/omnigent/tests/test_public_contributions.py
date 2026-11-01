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
        self.assertEqual(repository.fork, "lkshrk/omnigent")
        self.assertEqual(repository.ci, "github-hosted")

    def test_rejects_fork_outside_the_owner_account_or_equal_to_upstream(self):
        for fork in [
            "omnigent-ai/omnigent",
            "someone/omnigent",
            "lkshrk/other",
            "Lkshrk/omnigent",
            "lkshrk/omnigent.git",
            "https://github.com/lkshrk/omnigent.git",
            "lkshrk",
            "lkshrk/omnigent/extra",
            "",
            None,
        ]:
            with self.subTest(fork=fork):
                registry = self.registry()
                registry["repositories"]["omnigent"]["fork"] = fork

                with self.assertRaisesRegex(RegistryError, "fork"):
                    self.load(registry)

    def test_rejects_upstream_in_the_owner_account(self):
        registry = self.registry()
        entry = registry["repositories"]["omnigent"]
        entry["upstream"] = "https://github.com/lkshrk/omnigent.git"

        with self.assertRaisesRegex(RegistryError, "fork"):
            self.load(registry)

    def test_records_the_upstream_title_format(self):
        repository = Registry.load(REGISTRY_PATH).repository("omnigent")
        self.assertEqual(repository.contribution.title_format, "conventional-scoped")
        for value in ["conventional", "free", "", None]:
            with self.subTest(title_format=value):
                registry = self.registry()
                registry["repositories"]["omnigent"]["contribution"]["title_format"] = value

                with self.assertRaisesRegex(RegistryError, "title_format"):
                    self.load(registry)
        registry = self.registry()
        del registry["repositories"]["omnigent"]["contribution"]["title_format"]
        with self.assertRaisesRegex(RegistryError, "missing field: title_format"):
            self.load(registry)

    def test_requires_fork_and_github_hosted_ci(self):
        for field in ["fork", "ci"]:
            with self.subTest(missing=field):
                registry = self.registry()
                del registry["repositories"]["omnigent"][field]

                with self.assertRaisesRegex(RegistryError, f"missing field: {field}"):
                    self.load(registry)
        for ci in ["self-hosted", "cluster", True]:
            with self.subTest(ci=ci):
                registry = self.registry()
                registry["repositories"]["omnigent"]["ci"] = ci

                with self.assertRaisesRegex(RegistryError, "ci"):
                    self.load(registry)

    def test_rejects_unknown_field(self):
        registry = self.registry()
        registry["repositories"]["omnigent"]["unexpected"] = True

        with self.assertRaisesRegex(RegistryError, "unknown field"):
            self.load(registry)

    def test_rejects_duplicate_checkout(self):
        registry = self.registry()
        duplicate = copy.deepcopy(registry["repositories"]["omnigent"])
        registry["repositories"]["duplicate"] = duplicate

        with self.assertRaisesRegex(RegistryError, "duplicate checkout"):
            self.load(registry)

    def test_rejects_non_normalized_or_parent_checkout(self):
        for checkout in ["./omnigent", "nested/../omnigent", "../omnigent", "..\\omnigent"]:
            with self.subTest(checkout=checkout):
                registry = self.registry()
                registry["repositories"]["omnigent"]["checkout"] = checkout

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

    def test_rejects_invalid_registry_version_types(self):
        for version in [True, 1.0]:
            with self.subTest(version=version):
                registry = self.registry()
                registry["version"] = version

                with self.assertRaisesRegex(RegistryError, "version"):
                    self.load(registry)

    def test_rejects_non_public_package_registry(self):
        for registry_url in ["https:", "https://packages.example.invalid/private"]:
            with self.subTest(registry_url=registry_url):
                registry = self.registry()
                registry["repositories"]["omnigent"]["validation"]["package_registries"] = [registry_url]

                with self.assertRaisesRegex(RegistryError, "package_registries"):
                    self.load(registry)

    def test_rejects_shell_syntax_in_argv(self):
        for argv in ["pnpm run lint", ["pnpm", "run", "(lint)"], ["pnpm", "run", "lint*"], ["pnpm", "run", "lint\\\\"]]:
            with self.subTest(argv=argv):
                registry = self.registry()
                registry["repositories"]["omnigent"]["validation"]["commands"][0]["argv"] = argv

                with self.assertRaisesRegex(RegistryError, "argv|shell syntax"):
                    self.load(registry)


if __name__ == "__main__":
    unittest.main()
