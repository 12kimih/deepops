"""plugins/action/repo_package.py, run by Ansible on localhost under --check.

A package unknown to apt fails as apt does, unless a repository the same run adds is
pending (package_repos_unrefreshed): then the install is previewed as a change. A package
apt knows is apt's own result either way. With removals pending (host_baseline_unremoved)
a failed install is asked of apt again on dpkg's status without them: the host as the
run's removal leaves it."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import bounds  # noqa: E402
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
    - name: Known, refusing what it would not bring
      repo_package: {use: apt, name: dpkg, state: present, refuse: ["linux-image-"], refuse_reason: kernels}
      register: refusing
    - name: A refusal needs its reason
      repo_package: {use: apt, name: dpkg, state: present, refuse: ["linux-image-"]}
      register: no_reason
      ignore_errors: true
    - ansible.builtin.assert:
        that:
          - unknown is failed
          - pending is changed and pending is not failed
          - "'from example' in pending.diff.prepared"
          - known is not changed and known is not failed
          - after_removal is failed
          - "'apt still refuses' in after_removal.msg"
          - refusing is not failed and refusing is not changed
          - no_reason is failed and 'refuse_reason' in no_reason.msg
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


STATUS = """Package: keep
Status: install ok installed
Architecture: amd64

Package: old
Status: install ok installed
Architecture: i386

Package: old
Status: install ok installed
Architecture: amd64

Package: native
Status: install ok installed
Architecture: all

Package: other-arch
Status: install ok installed
Architecture: i386
"""


class Refuse(unittest.TestCase):
    def test_a_refused_package_is_named_with_the_reason(self):
        installs = [
            "libnvidia-gl-580-server",
            "linux-image-6.8.0-151-generic",
            "linux-modules-6.8.0-151-generic",
        ]
        self.assertEqual(rp.refused_in(installs, ["linux-image-"]), ["linux-image-6.8.0-151-generic"])
        self.assertEqual(rp.refused_in(["linux-image-x:amd64"], ["linux-image-x$"]), ["linux-image-x:amd64"])
        self.assertEqual(rp.refused_in(installs, []), [])
        failed = rp.refusal(["linux-image-6.8.0-151-generic"], "kernels change everywhere at once")
        self.assertTrue(failed["failed"])
        self.assertIn("linux-image-6.8.0-151-generic", failed["msg"])
        self.assertIn("kernels change everywhere at once", failed["msg"])


class Simulation(unittest.TestCase):
    def test_the_install_takes_the_tasks_arguments(self):
        self.assertEqual(
            rp.simulation_argv({"name": ["a", "b=1.0"], "install_recommends": False}),
            ["install", "--no-install-recommends", "a", "b=1.0"],
        )
        self.assertEqual(
            rp.simulation_argv({"name": "a, b", "default_release": "noble-backports"}),
            ["install", "-t", "noble-backports", "a", "b"],
        )

    def simulate(self, removals):
        """The command, run against STATUS with dpkg and apt-get standing in: apt-get
        prints its arguments and the status file it was given."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "status").write_text(STATUS)
            (base / "bin").mkdir()
            (base / "bin/dpkg").write_text("#!/bin/sh\necho amd64\n")
            (base / "bin/apt-get").write_text(
                '#!/bin/sh\necho "ARGS $*"\nfor a; do case "$a" in Dir::State::status=*) cat "${a#*=}";; esac; done\n'
            )
            for tool in ("dpkg", "apt-get"):
                (base / "bin" / tool).chmod(0o755)
            command = rp.simulation_command({"name": ["new"]}, removals, status=str(base / "status"))
            env = {**os.environ, "PATH": f"{base / 'bin'}:/usr/bin:/bin"}
            out = subprocess.run(
                ["/bin/sh", "-c", command],
                env=env,
                capture_output=True,
                text=True,
                timeout=bounds.CHILD,
                check=True,
            ).stdout
        return out

    def test_the_removed_packages_are_gone_from_the_status_apt_reads(self):
        out = self.simulate(["old:i386", "native", "absent"])
        self.assertIn("ARGS -s -q -o Dir::State::status=", out)
        self.assertTrue(out.splitlines()[0].endswith(" install new"))
        packages = [line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("Package: ")]
        # old:amd64 stays (only the i386 one goes); a bare name means the native arch or all.
        self.assertEqual(packages, ["keep", "old", "other-arch"])
        self.assertIn("Architecture: amd64", out.split("Package: old", 1)[1].split("\n\n", 1)[0])

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
