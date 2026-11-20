"""scripts/validation/role_defaults.py reads a role default as Ansible does, for every
plain scalar the roles define, so a validator's default is the deploy's value."""

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "validation"))
from role_defaults import role_default  # noqa: E402

sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402


class RoleDefaultTest(unittest.TestCase):
    def test_every_plain_scalar_reads_as_yaml_does(self):
        checked = 0
        for path in tracked("roles/*/defaults/main.yml"):
            role = path.parts[-3]
            for name, value in (yaml.safe_load(path.read_text()) or {}).items():
                # Single-token strings: what a validator reads (an image, a path, a name).
                if isinstance(value, str) and value and not any(c in value for c in " #'\"{"):
                    with self.subTest(role=role, name=name):
                        self.assertEqual(role_default(role, name), value)
                    checked += 1
        self.assertGreater(checked, 200)

    def test_the_validators_images(self):
        for name in ("ngc_ready_cuda_container", "validation_python_container"):
            self.assertRegex(role_default("deploy_defaults", name), r"@sha256:[0-9a-f]{64}$")

    def test_a_missing_name_is_an_error(self):
        with self.assertRaises(KeyError):
            role_default("deploy_defaults", "no_such_default")


if __name__ == "__main__":
    unittest.main()
