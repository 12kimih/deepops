"""roles/host_baseline/tasks/sysctl.yml: the boot-order reading and the removal pattern."""

import importlib.util
import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import render  # noqa: E402

spec = importlib.util.spec_from_file_location("sysctl", ROOT / "plugins" / "filter" / "sysctl.py")
sysctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sysctl)

KEY = "kernel.apparmor_restrict_unprivileged_userns"
# What a node printed: the role's file, then the administrator's /etc/sysctl.conf.
BOOT = f"""# /usr/lib/sysctl.d/10-apparmor.conf
{KEY} = 1

# /etc/sysctl.d/90-enroot.conf
user.max_user_namespaces=65536
{KEY}=1

# /etc/sysctl.d/99-sysctl.conf
# /etc/sysctl.conf - Configuration file for setting system variables
kernel/apparmor_restrict_unprivileged_userns=0
"""


def removal_pattern(key):
    tasks = yaml.safe_load((ROOT / "roles/host_baseline/tasks/sysctl.yml").read_text())
    task = next(t for t in tasks[0]["block"] if t["name"] == "Remove the other assignments of these keys")
    return render(task["ansible.builtin.lineinfile"]["regexp"], {"item": ["/etc/sysctl.conf", key]})


class BootOrder(unittest.TestCase):
    def test_the_last_file_wins(self):
        self.assertEqual(
            sysctl.sysctl_boot_values(BOOT)[KEY], {"value": "0", "file": "/etc/sysctl.d/99-sysctl.conf"}
        )

    def test_a_later_file_undoing_the_role_is_reported(self):
        self.assertEqual(
            sysctl.sysctl_boot_mismatches(BOOT, {KEY: "1", "user.max_user_namespaces": 65536}),
            [f"{KEY}: boot applies 0 from /etc/sysctl.d/99-sysctl.conf, not 1"],
        )

    def test_a_missing_key_is_reported(self):
        self.assertEqual(
            sysctl.sysctl_boot_mismatches("", {"vm.x": "1 2"}), ["vm.x: boot applies nothing, not 1 2"]
        )


class Removal(unittest.TestCase):
    def test_the_pattern_matches_every_spelling_of_the_key_only(self):
        pattern = re.compile(removal_pattern(KEY))
        for line in (
            f"{KEY}=0",
            f"  {KEY} = 0",
            f"-{KEY}=0",
            "kernel/apparmor_restrict_unprivileged_userns=0",
        ):
            self.assertTrue(pattern.search(line), line)
        for line in (f"# {KEY}=0", f"{KEY}_other=0", f"x{KEY}=0"):
            self.assertFalse(pattern.search(line), line)


if __name__ == "__main__":
    unittest.main()
