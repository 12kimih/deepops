"""os-updates-metrics keeps its "pending since" clock across a run that measured nothing.

The alert counts days since security updates were first seen pending. A run where apt
could not simulate the upgrade knows nothing about them; resetting the clock there
restarted the count after every transient apt failure.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "roles/os_updates/files/os-updates-metrics"


class PendingSinceTests(unittest.TestCase):
    def run_with(self, apt_script):
        (self.bin / "apt-get").write_text("#!/bin/sh\n" + apt_script)
        (self.bin / "apt-get").chmod(0o755)
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", OS_UPDATES_STATE=str(self.state),
                   OS_UPDATES_RUN=str(self.state))  # fmt: skip
        subprocess.run(["sh", str(SCRIPT), str(self.textfile)], env=env, check=True, capture_output=True)
        since = self.state / "security-pending-since"
        return since.read_text().strip() if since.exists() else None

    def test_the_clock_survives_an_apt_failure_and_ends_when_nothing_is_pending(self):
        with tempfile.TemporaryDirectory() as d:
            self.bin, self.state, self.textfile = Path(d, "bin"), Path(d, "state"), Path(d, "textfile")
            self.bin.mkdir()
            (self.bin / "dpkg").write_text("#!/bin/sh\nexit 0\n")
            (self.bin / "dpkg").chmod(0o755)
            first = self.run_with("echo 'Inst libfoo (1 Ubuntu:24.04/noble-security [amd64])'\n")
            self.assertIsNotNone(first)
            self.assertEqual(self.run_with("exit 100\n"), first, "an apt failure keeps the clock")
            self.assertEqual(
                self.run_with("echo 'Inst libfoo (1 Ubuntu:24.04/noble-security [amd64])'\n"), first
            )
            self.assertIsNone(self.run_with("exit 0\n"), "nothing pending ends it")


class PackageInfoTests(unittest.TestCase):
    def test_every_installed_package_is_one_series_and_removed_ones_are_not(self):
        with tempfile.TemporaryDirectory() as d:
            bin_, state, textfile = Path(d, "bin"), Path(d, "state"), Path(d, "textfile")
            bin_.mkdir()
            stubs = {
                "dpkg": "exit 0\n",
                "apt-get": "exit 0\n",
                "dpkg-query": "printf 'ii |libc6:amd64|2.39-0ubuntu8.6\\nrc |oldpkg|1.0\\nii |linux-image-generic|6.8.0-149.149\\n'\n",
            }
            for name, body in stubs.items():
                (bin_ / name).write_text("#!/bin/sh\n" + body)
                (bin_ / name).chmod(0o755)
            env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", OS_UPDATES_STATE=str(state),
                       OS_UPDATES_RUN=str(state))  # fmt: skip
            subprocess.run(["sh", str(SCRIPT), str(textfile)], env=env, check=True, capture_output=True)
            lines = [
                line
                for line in (textfile / "os-updates.prom").read_text().splitlines()
                if line.startswith("os_updates_package_info")
            ]
            self.assertEqual(
                lines,
                [
                    'os_updates_package_info{package="libc6:amd64",version="2.39-0ubuntu8.6"} 1',
                    'os_updates_package_info{package="linux-image-generic",version="6.8.0-149.149"} 1',
                ],
            )


if __name__ == "__main__":
    unittest.main()


class BootRepublishTests(unittest.TestCase):
    def test_the_boot_republish_does_not_report_the_reboot_it_followed_as_pending(self):
        # Measured with a reboot pending, then republished after the reboot (the flag on
        # /run is gone): the kept copy said 1, which kept OsRebootPending firing.
        with tempfile.TemporaryDirectory() as d:
            bin_, state, run, textfile = (Path(d, n) for n in ("bin", "state", "run", "textfile"))
            for path in (bin_, run):
                path.mkdir()
            for name in ("dpkg", "apt-get", "dpkg-query"):
                (bin_ / name).write_text("#!/bin/sh\nexit 0\n")
                (bin_ / name).chmod(0o755)
            flag = run / "reboot-required"
            env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", OS_UPDATES_STATE=str(state),
                       OS_UPDATES_RUN=str(run), OS_UPDATES_REBOOT_FLAG=str(flag))  # fmt: skip

            def published(*args):
                subprocess.run(
                    ["sh", str(SCRIPT), *args, str(textfile)], env=env, check=True, capture_output=True
                )
                return dict(
                    line.split(" ", 1)
                    for line in (textfile / "os-updates.prom").read_text().splitlines()
                    if line.startswith("os_updates_reboot_required")
                )

            flag.touch()
            before = published()
            self.assertEqual(before["os_updates_reboot_required"], "1")
            flag.unlink()
            (run / "os-updates-reboot-required-since").unlink()  # /run is empty after a reboot
            after = published("--publish")
            self.assertEqual(
                after, {"os_updates_reboot_required": "0", "os_updates_reboot_required_since_seconds": "0"}
            )
