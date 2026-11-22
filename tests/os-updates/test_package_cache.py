"""roles/os_updates keeps no downloaded package: apt-get and python-apt (Ansible's apt
module, unattended-upgrades) leave every .deb in /var/cache/apt/archives, and the daily
apt job cleans nothing while its intervals are 0 (3.5G on a GPU node, 2026-11). Both
managed policies (security, off) set it; unmanaged leaves the distribution's settings."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import load, same_name  # noqa: E402

TASK = next(
    t
    for t in load("roles/os_updates/tasks/debian.yml")
    if same_name(t.get("name"), "Keep no downloaded packages")
)
SETTINGS = dict(re.findall(r'^(\S+) "([^"]*)";$', TASK["copy"]["content"], re.M))


class PackageCache(unittest.TestCase):
    def test_installs_delete_what_they_downloaded(self):
        self.assertEqual(SETTINGS["APT::Keep-Downloaded-Packages"], "false")

    def test_the_daily_job_empties_the_cache(self):
        self.assertEqual(SETTINGS["APT::Periodic::CleanInterval"], "1")

    def test_whatever_the_update_policy(self):
        self.assertNotIn("when", TASK)
        self.assertIn("apt-config", TASK["copy"]["validate"])


if __name__ == "__main__":
    unittest.main()
