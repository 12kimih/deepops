"""plugins/filter/mounts.py: the mount point a path lives on."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "filter"))
from mounts import mount_point  # noqa: E402

MOUNTS = [{"mount": m} for m in ("/", "/var", "/var/tmp", "/boot/efi", "/raid")]


class MountPoint(unittest.TestCase):
    def test_the_deepest_mount_above(self):
        self.assertEqual(mount_point("/var/spool/slurmd-private", MOUNTS), "/var")
        self.assertEqual(mount_point("/var/tmp/x", MOUNTS), "/var/tmp")
        self.assertEqual(mount_point("/raid", MOUNTS), "/raid")
        self.assertEqual(mount_point("/srv/data", MOUNTS), "/")

    def test_whole_components_only(self):
        self.assertEqual(mount_point("/var/tmpfoo", MOUNTS), "/var")
        self.assertEqual(mount_point("/raidx/a", MOUNTS), "/")

    def test_a_relative_path_or_no_root_is_refused(self):
        with self.assertRaises(ValueError):
            mount_point("var/tmp", MOUNTS)
        with self.assertRaises(ValueError):
            mount_point("/srv", [{"mount": "/var"}])


if __name__ == "__main__":
    unittest.main()
