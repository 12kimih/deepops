"""When roles/os_updates measures pending updates: daily, after every dpkg run, and at
every boot, publishing the kept state first. A host upgraded at 10:46 and rebooted at
10:57 still reported its 00:21 measurement (2026-11-23): the boot unit only republished
the kept pre-upgrade data, and the timer's OnBootSec trigger never fired after a
daemon-reload."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import load, same_name  # noqa: E402

TASKS = load("roles/os_updates/tasks/debian.yml")


def flat(tasks):
    for task in tasks:
        yield task
        yield from flat(task.get("block", []))


def task(name):
    found = [t for t in flat(TASKS) if same_name(t.get("name"), name)]
    assert len(found) == 1, name
    return found[0]


UNITS = task("Install the pending-updates metrics units")["vars"]["units"]


class Triggers(unittest.TestCase):
    def test_every_dpkg_run_starts_a_measurement_without_waiting(self):
        hook = task("Measure again after every package change")["copy"]
        self.assertEqual(hook["dest"], "/etc/apt/apt.conf.d/54deepops-os-updates-metrics")
        line = re.search(r'DPkg::Post-Invoke \{ "(.+)"; \};', hook["content"])[1]
        self.assertIn("systemctl start --no-block os-updates-metrics.service", line)
        self.assertTrue(line.endswith("|| true"), "a host without systemd still installs packages")

    def test_boot_publishes_then_measures_after_the_network(self):
        boot = UNITS["publish.service"]
        starts = re.findall(r"^ExecStart=(.+)$", boot, re.M)
        self.assertEqual(len(starts), 2)
        self.assertIn("--publish", starts[0])
        self.assertNotIn("--publish", starts[1])
        self.assertIn("After=network-online.target", boot)
        self.assertIn("WantedBy=multi-user.target", boot)

    def test_the_timer_needs_no_boot_trigger(self):
        self.assertNotIn("OnBootSec", UNITS["timer"])
        self.assertIn("OnCalendar=daily", UNITS["timer"])

    def test_turning_the_metrics_off_removes_the_hook(self):
        gone = task("Remove the metrics command, units and kept metrics")["loop"]
        self.assertIn("/etc/apt/apt.conf.d/54deepops-os-updates-metrics", gone)


if __name__ == "__main__":
    unittest.main()
