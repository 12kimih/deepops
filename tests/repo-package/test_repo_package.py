"""plugins/action/repo_package.py, run by Ansible on localhost under --check.

A package unknown to apt fails as apt does, unless a repository the same run adds is
pending (package_repos_unrefreshed): then the install is previewed as a change. A package
apt knows is apt's own result either way."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import unrecorded  # noqa: E402

PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")

PLAY = """
- hosts: localhost
  gather_facts: false
  tasks:
    - name: Unknown, nothing pending
      repo_package: {use: apt, name: deepops-no-such-package, state: present}
      register: unknown
      ignore_errors: true
    - ansible.builtin.set_fact:
        package_repos_unrefreshed: [example]
    - name: Unknown, a repository pending (the package manager found by itself)
      repo_package: {name: deepops-no-such-package, state: present}
      register: pending
    - name: Known and installed
      repo_package: {use: apt, name: dpkg, state: present}
      register: known
    - ansible.builtin.assert:
        that:
          - unknown is failed
          - pending is changed and pending is not failed
          - "'from example' in pending.diff.prepared"
          - known is not changed and known is not failed
"""


@unittest.skipUnless(Path("/usr/bin/apt-get").exists(), "needs apt")
class RepoPackage(unittest.TestCase):
    def test_a_pending_repository_turns_the_failure_into_a_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            play = Path(tmp) / "play.yml"
            play.write_text(PLAY)
            done = subprocess.run(
                [str(PLAYBOOK), "-i", "localhost,", "-c", "local", "--check", str(play)],
                cwd=ROOT, capture_output=True, text=True, env=unrecorded.env(), timeout=unrecorded.TIMEOUT,
            )  # fmt: skip
            self.assertEqual(done.returncode, 0, done.stdout[-3000:] + done.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
