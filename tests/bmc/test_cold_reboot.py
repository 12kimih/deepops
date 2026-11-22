"""roles/bmc/files/bmc-cold-reboot: a reboot of a host that needs a cold start ends in a
chassis power cycle by its own BMC, and nothing else does.

A host whose GPUs drop off the bus on a warm reboot came back short after every Slurm
reboot. The hook runs last in systemd-shutdown, so every clean reboot shuts down first
and then is turned into a power cycle; a poweroff must stay a poweroff, and a host
without an IPMI device (or a BMC that does not answer) must still reboot.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import bounds  # noqa: E402

TEMPLATE = ROOT / "roles/bmc/templates/bmc-cold-reboot.j2"
DEFAULTS = yaml.safe_load((ROOT / "roles/deepops_paths/defaults/main.yml").read_text())


def render(tmp, off=30):
    """The hook as the role writes it (the template uses plain Jinja and format)."""
    import jinja2

    env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    path = Path(tmp, "bmc-cold-reboot")
    text = env.from_string(TEMPLATE.read_text()).render(
        ansible_managed="managed",
        bmc_cold_reboot_off_seconds=off,
        deepops_system_path=DEFAULTS["deepops_system_path"],
    )
    path.write_text(text)
    path.chmod(0o755)
    return path


def sel(text):
    """The Add SEL Entry call the hook makes for TEXT: an OEM e0 record of 13 bytes."""
    data = " ".join(f"0x{b:02x}" for b in f"dpcr {text}".ljust(13)[:13].encode())
    return f"ipmitool -I open raw 0x0a 0x44 0x00 0x00 0xe0 {data}"


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
                "BMC_COLD_REBOOT_PATH": f"{bin_dir}:/usr/bin:/bin",
                # a character device every host has, standing in for /dev/ipmi0
                "BMC_COLD_REBOOT_DEVICE": "/dev/null" if device else str(Path(d, "none")),
            }
            done = subprocess.run(
                ["sh", str(render(d)), verb],
                env=env,
                capture_output=True,
                text=True,
                timeout=bounds.CHILD,
                check=False,
            )
            return done.returncode, calls.read_text().splitlines() if calls.exists() else []

    def test_a_reboot_records_its_start_sets_the_off_time_then_cycles_and_waits(self):
        status, calls = self.run_hook("reboot")
        self.assertEqual(status, 0)
        ipmi = [c for c in calls if c.startswith("ipmitool")]
        self.assertEqual(ipmi[0], sel("start-reboot"))  # first, before any test
        self.assertEqual(ipmi[1], "ipmitool -I open raw 0x00 0x0b 0x1e")  # 30 s off
        self.assertEqual(ipmi[2], sel("cycling"))
        self.assertEqual(ipmi[3], "ipmitool -I open chassis power cycle")
        self.assertEqual(calls[-1], "sleep 45")

    def test_the_off_time_is_one_byte_of_seconds(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("raw 0x00 0x0b 0x50", render(d, off=80).read_text())

    def test_poweroff_halt_and_kexec_are_recorded_and_left_alone(self):
        for verb in ("poweroff", "halt", "kexec"):
            status, calls = self.run_hook(verb)
            self.assertEqual(
                (status, [c for c in calls if c.startswith("ipmitool")]), (0, [sel(f"start-{verb}")])
            )

    def test_no_ipmi_device_is_a_plain_reboot_and_says_so(self):
        status, calls = self.run_hook("reboot", device=False)
        self.assertEqual(status, 0)
        self.assertEqual([c for c in calls if c.startswith("ipmitool")][-1], sel("no-dev"))

    def test_a_bmc_that_does_not_answer_lets_the_warm_reboot_go_on(self):
        status, calls = self.run_hook("reboot", ipmitool_status=1)
        self.assertEqual(status, 0)
        self.assertNotIn("sleep 45", calls)
        self.assertEqual([c for c in calls if c.startswith("ipmitool")][-1], sel("no-answer"))

    def test_systemd_shutdown_can_run_it_within_its_hook_timeout(self):
        # systemd-shutdown kills a hook after 90 s: three BMC calls of at most 10 s and
        # the wait stay under, and the role installs it executable.
        with tempfile.TemporaryDirectory() as d:
            text = render(d).read_text()
        self.assertIn("timeout 5 ipmitool", text)
        self.assertIn(":-45}", text)
        self.assertLess(5 * 5 + 45, 90)
        tasks = (ROOT / "roles/bmc/tasks/main.yml").read_text()
        self.assertIn("src: bmc-cold-reboot.j2", tasks)
        self.assertIn('mode: "0755"', tasks)


if __name__ == "__main__":
    unittest.main()
