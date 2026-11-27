"""roles/textfile_keep, tasks_from: unprivileged: the textfile directory of the producers
that do not run as root is node_exporter_unprivileged_group's (setgid and sticky) once
that group is on the host, and root's before (nothing but root then writes it). Run on
localhost under --check: nothing is changed."""

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
        tasks_from: unprivileged
    - ansible.builtin.debug:
        msg: "WRITER={{ _textfile_keep_unprivileged_writer }}"
"""


class UnprivilegedDir(unittest.TestCase):
    def writer(self, group):
        base = local_scratch.directory("unprivileged-dir")
        self.addCleanup(local_scratch.remove, base)
        (base / "play.yml").write_text(PLAY)
        extra = {
            "node_exporter_unprivileged_dir": str(base / "textfile"),
            "node_exporter_unprivileged_group": group,
        }
        done = subprocess.run(
            ["ansible-playbook", "-i", "localhost,", str(base / "play.yml"), "--check", "-e", json.dumps(extra)],
            cwd=ROOT, capture_output=True, text=True, env=unrecorded.env(), timeout=unrecorded.TIMEOUT,
        )  # fmt: skip
        self.assertEqual(done.returncode, 0, done.stdout[-3000:] + done.stderr[-2000:])
        self.assertFalse((base / "textfile").exists(), "--check makes nothing")
        return re.search(r"WRITER=([\w.-]+)", done.stdout).group(1)

    def test_the_group_writes_it_once_it_is_on_the_host(self):
        group = grp.getgrgid(os.getgid()).gr_name  # any group the host's files know
        self.assertEqual(self.writer(group), group)

    def test_before_the_group_exists_it_is_roots(self):
        self.assertEqual(self.writer("no-such-group-x"), "root")


if __name__ == "__main__":
    unittest.main()
