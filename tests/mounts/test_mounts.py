"""plugins/filter/mounts.py: the mount point a path lives on."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "lib"))
import in_tree  # noqa: E402

# One file loaded as a module: plugins/filter on sys.path would shadow the standard
# library with its secrets.py (tests/python-rules/test_filter_path).
_mounts = in_tree.load("plugins/filter/mounts.py")
fstab_entries, mount_point = _mounts.fstab_entries, _mounts.mount_point

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


FSTAB = """# /etc/fstab: static file system information.
UUID=7fd8 / ext4 defaults,noatime,errors=remount-ro 0 1
UUID=731f none swap sw 0 0
/dev/sdb1 /home ext4 defaults
server:/scratch /scratch nfs rw,hard,_netdev  # a comment
/dev/sdc1 /data\\040set xfs noatime 0 2
"""


class FstabEntries(unittest.TestCase):
    def test_each_entry_in_order_with_mounts_defaults(self):
        entries = fstab_entries(FSTAB)
        self.assertEqual([e["path"] for e in entries], ["/", "none", "/home", "/scratch", "/data\\040set"])
        self.assertEqual(entries[0]["opts"], "defaults,noatime,errors=remount-ro")
        self.assertEqual((entries[2]["dump"], entries[2]["passno"]), ("0", "0"), "mount(8) reads 0")
        self.assertEqual(entries[3]["opts"], "rw,hard,_netdev", "the comment is not an option")

    def test_a_filesystem_another_host_serves_is_marked(self):
        self.assertEqual([e["path"] for e in fstab_entries(FSTAB) if e["network"]], ["/scratch"])


class ProjectFilesystems(unittest.TestCase):
    def test_each_project_gets_the_mount_it_lives_on(self):
        project_filesystems = _mounts.project_filesystems

        got = project_filesystems({"/var/tmp/x": {"id": 7}, "/srv/d": {"id": 8}}, MOUNTS)
        self.assertEqual(
            got, {"/var/tmp/x": {"id": 7, "filesystem": "/var/tmp"}, "/srv/d": {"id": 8, "filesystem": "/"}}
        )
        self.assertEqual(project_filesystems({}, MOUNTS), {})


if __name__ == "__main__":
    unittest.main()
