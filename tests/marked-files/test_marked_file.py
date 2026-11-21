"""roles/marked_files: a file goes only where it shows deepops made it (run by Ansible)."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import unrecorded  # noqa: E402


def run(paths, check=False):
    argv = ["ansible", "localhost", "-M", "roles/marked_files/library", "-m", "marked_file",
            "-a", json.dumps({"paths": [str(p) for p in paths]})] + (["--check", "--diff"] if check else [])  # fmt: skip
    env = unrecorded.env(ANSIBLE_LOAD_CALLBACK_PLUGINS="1", ANSIBLE_STDOUT_CALLBACK="ansible.builtin.json")
    done = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, env=env)
    return json.loads(done.stdout)["plays"][0]["tasks"][0]["hosts"]["localhost"]


class MarkedFile(unittest.TestCase):
    def test_only_marked_files_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            ours, theirs = Path(tmp) / "cron.allow", Path(tmp) / "hand.allow"
            ours.write_text("# Managed by roles/node_user_policy\nroot\n")
            theirs.write_text("root\nalice\n")
            preview = run([ours, theirs, Path(tmp) / "missing"], check=True)
            self.assertTrue(ours.exists(), "--check removes nothing")
            self.assertEqual(preview["diff"], {"prepared": f"remove {ours}\n"}, "the preview says which")
            got = run([ours, theirs, Path(tmp) / "missing"])
            self.assertEqual((got["removed"], got["kept"]), ([str(ours)], [str(theirs)]))
            self.assertEqual(preview["removed"], got["removed"])
            self.assertFalse(ours.exists())
            self.assertTrue(theirs.exists(), "a site's own list stays")
            self.assertFalse(run([ours, theirs])["changed"])


if __name__ == "__main__":
    unittest.main()
