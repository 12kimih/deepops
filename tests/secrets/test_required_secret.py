"""required_secret refuses empty and placeholder secrets (plugins/filter/secrets.py)."""

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("secrets_filter", ROOT / "plugins" / "filter" / "secrets.py")
secrets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(secrets)


class RequiredSecret(unittest.TestCase):
    def test_placeholders_are_refused(self):
        for value in (
            "",
            None,
            "ReplaceWithASecurePasswordInTheVault",
            "AlsoReplaceWithASecurePasswordInTheVault",
            "deepops",
            "changeme",
            " Password ",
        ):
            self.assertTrue(secrets.is_placeholder(value), value)

    def test_real_values_pass(self):
        for value in ("x9!kQ2-long", "deepops-2026-random"):
            self.assertFalse(secrets.is_placeholder(value), value)

    def test_role_defaults_and_example_are_placeholders(self):
        # The shipped values must be refused, or a site that forgets to set them deploys them.
        # config.example shows them set or commented: either way a site copying it gets that.
        sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
        import settings

        for path, names in (
            ("roles/slurm/defaults/main.yml", ("slurm_password", "slurm_db_password")),
            ("roles/grafana/defaults/main.yml", ("grafana_cfg_pass",)),
            ("config.example/group_vars/slurm-cluster.yml", ("slurm_password", "slurm_db_password")),
        ):
            data = {e.name: e.value for e in settings.entries((ROOT / path).read_text())}
            for name in names:
                self.assertTrue(secrets.is_placeholder(data[name]), f"{path}: {name}")


if __name__ == "__main__":
    unittest.main()
