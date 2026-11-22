"""roles/bmc/files/bmc-cold-reboot: a reboot of a host that needs a cold start ends in a
chassis power cycle by its own BMC, and nothing else does.

A host whose GPUs drop off the bus on a warm reboot came back short after every Slurm
reboot. The hook runs last in systemd-shutdown, so every clean reboot shuts down first
and then is turned into a power cycle; a poweroff must stay a poweroff, and a host
without an IPMI device (or a BMC that does not answer) must still reboot.
"""

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import bounds  # noqa: E402

HOOK = ROOT / "roles/bmc/files/bmc-cold-reboot"


class ColdReboot(unittest.TestCase):
    def run_hook(self, verb, device=True, ipmitool_status=0):
        with tempfile.TemporaryDirectory() as d:
            calls = Path(d, "calls")
            bin_dir = Path(d, "bin")
            bin_dir.mkdir()
            for name, body in {
                "ipmitool": f'echo "ipmitool $*" >> {calls}\nexit {ipmitool_status}',
                "timeout": f'echo "timeout $1" >> {calls}\nshift\nexec "$@"',
                "sleep": f'echo "sleep $*" >> {calls}',
            }.items():
                path = bin_dir / name
                path.write_text(f"#!/bin/sh\n{body}\n")
                path.chmod(0o755)
            env = {
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                # a character device every host has, standing in for /dev/ipmi0
                "BMC_COLD_REBOOT_DEVICE": "/dev/null" if device else str(Path(d, "none")),
            }
            done = subprocess.run(
                ["sh", str(HOOK), verb],
                env=env,
                capture_output=True,
                text=True,
                timeout=bounds.CHILD,
                check=False,
            )
            return done.returncode, calls.read_text().splitlines() if calls.exists() else []

    def test_a_reboot_asks_the_bmc_for_a_power_cycle_and_waits(self):
        status, calls = self.run_hook("reboot")
        self.assertEqual(status, 0)
        self.assertEqual(calls, ["timeout 20", "ipmitool -I open chassis power cycle", "sleep 60"])

    def test_poweroff_halt_and_kexec_are_left_alone(self):
        for verb in ("poweroff", "halt", "kexec"):
            self.assertEqual(self.run_hook(verb), (0, []), verb)

    def test_no_ipmi_device_is_a_plain_reboot(self):
        self.assertEqual(self.run_hook("reboot", device=False), (0, []))

    def test_a_bmc_that_does_not_answer_lets_the_warm_reboot_go_on(self):
        status, calls = self.run_hook("reboot", ipmitool_status=1)
        self.assertEqual(status, 0)
        self.assertNotIn("sleep 60", calls)

    def test_systemd_shutdown_can_run_it_within_its_hook_timeout(self):
        # systemd-shutdown kills a hook after 90 s; the BMC call and the wait stay under.
        self.assertTrue(HOOK.stat().st_mode & stat.S_IXUSR)
        text = HOOK.read_text()
        self.assertIn("timeout 20 ipmitool", text)
        self.assertIn(":-60}", text)
        self.assertLess(20 + 60, 90)
        self.assertTrue(os.access(HOOK, os.X_OK))


if __name__ == "__main__":
    unittest.main()
