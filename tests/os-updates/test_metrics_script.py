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
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", OS_UPDATES_STATE=str(self.state))
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


if __name__ == "__main__":
    unittest.main()
