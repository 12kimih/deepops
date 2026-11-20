"""roles/site_secrets/library/site_secret.py run as Ansible runs it, on scratch paths."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "roles/site_secrets/library/site_secret.py"


def run(**args):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"ANSIBLE_MODULE_ARGS": args}, f)
    done = subprocess.run([sys.executable, str(MODULE), f.name], capture_output=True, text=True)
    Path(f.name).unlink()
    return json.loads(done.stdout)


class Password(unittest.TestCase):
    def test_a_printable_password_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ldap" / "sssd.password"
            first = run(path=str(path), kind="password", length=32, vault=False)
            self.assertTrue(first["changed"], first)
            text = path.read_text()
            self.assertRegex(text, r"^[A-Za-z0-9_-]{43}$", "URL-safe, no newline")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            again = run(path=str(path), kind="password", length=32, vault=False)
            self.assertFalse(again["changed"])
            self.assertEqual(path.read_text(), text, "never replaced")

    def test_too_short(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = run(path=f"{tmp}/p", kind="password", length=8, vault=False)
            self.assertTrue(got.get("failed"))


if __name__ == "__main__":
    unittest.main()
