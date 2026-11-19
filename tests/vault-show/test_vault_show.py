"""vault_show.py finds an inline !vault value in config/ and decrypts it.

ansible-core 2.19+ redacts vaulted values in debug output, so this is the way
the docs show a secret. Uses a throwaway config and vault password.
"""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deepops" / "vault_show.py"
VAULT = shutil.which("ansible-vault", path=str(Path(sys.executable).parent)) or "ansible-vault"


class VaultShow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        (base / "pass").write_text("test-password\n")
        self.env = dict(
            os.environ,
            DEEPOPS_CONFIG_DIR=str(base / "config"),
            ANSIBLE_VAULT_PASSWORD_FILE=str(base / "pass"),
        )
        for d in ("group_vars", "host_vars"):
            (base / "config" / d).mkdir(parents=True)
        self.base = base

    def vaulted(self, name, value):
        return subprocess.run(
            [VAULT, "encrypt_string", value, "--name", name],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def run_script(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], env=self.env, capture_output=True, text=True
        )

    def test_decrypts_a_group_var(self):
        (self.base / "config/group_vars/all.yml").write_text(
            "---\nplain: 1\n" + self.vaulted("secret", "s3cr3t")
        )
        result = self.run_script("secret")
        self.assertEqual((result.returncode, result.stdout), (0, "s3cr3t\n"), result.stderr)

    def test_several_definitions_need_a_host(self):
        (self.base / "config/group_vars/all.yml").write_text(self.vaulted("secret", "group"))
        (self.base / "config/group_vars/other.yml").write_text(self.vaulted("secret", "other"))
        (self.base / "config/host_vars/node1").write_text(self.vaulted("secret", "host"))
        self.assertIn("pass --host", self.run_script("secret").stderr)
        self.assertEqual(self.run_script("secret", "--host", "node1").stdout, "host\n")

    def test_missing_name_fails(self):
        (self.base / "config/group_vars/all.yml").write_text("plain: 1\n")
        result = self.run_script("secret")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no !vault value", result.stderr)


if __name__ == "__main__":
    unittest.main()
