"""roles/textfile_keep, tasks_from: expected: the directory of the expected component
state is writable by the controller user's group only when that group is the user's own
private one on the host; any other group (a shared primary group, root) leaves it
root's, and the run says so. Run on localhost under --check: nothing is changed."""

import getpass
import grp
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import local_scratch  # noqa: E402
import unrecorded  # noqa: E402

PLAY = """
- hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - ansible.builtin.include_role:
        name: textfile_keep
        tasks_from: expected
    - ansible.builtin.debug:
        msg: "WRITER={{ _textfile_keep_expected_writer }}"
"""


class ExpectedDir(unittest.TestCase):
    def writer(self, group):
        base = local_scratch.directory("expected-dir")
        self.addCleanup(local_scratch.remove, base)
        (base / "play.yml").write_text(PLAY)
        extra = {"component_expected_dir": str(base / "expected"), "component_expected_group": group,
                 "component_expected_user": getpass.getuser()}  # fmt: skip
        done = subprocess.run(
            ["ansible-playbook", "-i", "localhost,", str(base / "play.yml"), "--check", "-e", json.dumps(extra)],
            cwd=ROOT, capture_output=True, text=True, env=unrecorded.env(), timeout=unrecorded.TIMEOUT,
        )  # fmt: skip
        self.assertEqual(done.returncode, 0, done.stdout[-3000:] + done.stderr[-2000:])
        self.assertFalse((base / "expected").exists(), "--check makes nothing")
        return re.search(r"WRITER=([\w.-]+)", done.stdout).group(1), done.stdout

    def test_a_group_the_user_does_not_own_leaves_it_roots_and_says_so(self):
        writer, out = self.writer("root")
        self.assertEqual(writer, "root")
        shared = next(
            (g.gr_name for g in grp.getgrall() if g.gr_name != getpass.getuser() and g.gr_name != "root"),
            None,
        )
        if shared:
            writer, out = self.writer(shared)
            self.assertEqual(writer, "root", shared)
            self.assertIn("cannot write it", out)

    def test_the_users_own_private_group_writes_it(self):
        own = grp.getgrgid(os.getgid())
        if own.gr_name != getpass.getuser() or set(own.gr_mem) - {getpass.getuser()}:
            self.skipTest("this user's primary group is not a private one")
        self.assertEqual(self.writer(own.gr_name)[0], own.gr_name)


if __name__ == "__main__":
    unittest.main()
