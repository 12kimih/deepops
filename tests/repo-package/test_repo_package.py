"""plugins/action/repo_package.py, run by Ansible on localhost under --check.

A package unknown to apt fails as apt does, unless a repository the same run adds is
pending (package_repos_unrefreshed): then the install is previewed as a change. A package
apt knows is apt's own result either way. With removals pending (host_baseline_unremoved)
a failed install is asked of apt again with them in the same transaction."""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import unrecorded  # noqa: E402

PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
spec = importlib.util.spec_from_file_location("repo_package", ROOT / "plugins" / "action" / "repo_package.py")
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)

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
    - ansible.builtin.set_fact:
        package_repos_unrefreshed: []
        host_baseline_unremoved: [deepops-no-such-leftover]
    - name: Unknown, a removal pending
      repo_package: {use: apt, name: deepops-no-such-package, state: present}
      register: after_removal
      ignore_errors: true
    - ansible.builtin.assert:
        that:
          - unknown is failed
          - pending is changed and pending is not failed
          - "'from example' in pending.diff.prepared"
          - known is not changed and known is not failed
          - after_removal is failed
          - "'apt still refuses' in after_removal.msg"
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


class Simulation(unittest.TestCase):
    def test_the_install_and_the_removals_are_one_transaction(self):
        self.assertEqual(
            rp.simulation_argv({"name": ["a", "b=1.0"], "install_recommends": False}, ["old:i386"]),
            ["apt-get", "-s", "-q", "install", "--no-install-recommends", "a", "b=1.0", "old:i386-"],
        )
        self.assertEqual(
            rp.simulation_argv({"name": "a, b", "default_release": "noble-backports"}, ["x"]),
            ["apt-get", "-s", "-q", "install", "-t", "noble-backports", "a", "b", "x-"],
        )

    def test_apts_answer_is_the_preview(self):
        failed = {"failed": True, "msg": "held broken packages"}
        ok = rp.with_removals(
            failed, ["old:i386"], {"rc": 0, "stdout": "Remv old:i386 [1]\nInst new (2 archive)\n"}
        )
        self.assertTrue(ok["changed"])
        self.assertNotIn("failed", ok)
        self.assertEqual(ok["diff"]["prepared"], "would install new\n")
        self.assertEqual(ok["repo_package_installs"], ["new"])
        refused = rp.with_removals(failed, ["old"], {"rc": 100, "stdout": "", "stderr": "E: unmet"})
        self.assertTrue(refused["failed"])
        self.assertIn("E: unmet", refused["msg"])

    def test_a_refusal_names_apts_reason_not_the_connections_notice(self):
        # become with a password runs over a pty: apt's own output comes on stdout and
        # ssh's closing notice on stderr, which once hid the reason.
        stdout = (
            "Reading package lists...\nSome packages could not be installed.\n"
            "The following packages have unmet dependencies:\n"
            " libnvidia-gl-580-server : Depends: libnvidia-egl-wayland1\n"
            "E: Unable to correct problems, you have held broken packages.\n"
        )
        simulated = {"rc": 100, "stdout": stdout, "stderr": "Shared connection to host closed.\n"}
        refused = rp.with_removals({"failed": True, "msg": "m"}, ["old:i386"], simulated)
        self.assertIn("libnvidia-gl-580-server : Depends: libnvidia-egl-wayland1", refused["msg"])
        self.assertIn("E: Unable to correct problems", refused["msg"])
        self.assertNotIn("Shared connection", refused["msg"])
        self.assertEqual(refused["repo_package_simulation"]["rc"], 100)

    def test_installs_are_what_apt_would_install_or_upgrade(self):
        out = "Remv old [1]\nInst a (2 archive [amd64])\nInst b:i386 [1] (2 archive [i386])\nConf a (2)\n"
        self.assertEqual(rp.installs_of(out), ["a", "b:i386"])
        self.assertEqual(rp.installs_of(None), [])


if __name__ == "__main__":
    unittest.main()
