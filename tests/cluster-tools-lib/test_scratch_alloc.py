"""cluster_tools.scratch_alloc: one allocation per person, /scratch/<login>, made (or
taken as it is when theirs) with a project quota whose ID is their UID; resize, extend
and release act on the registry and the quota; at the end date it freezes at what it
holds, and after the grace period, or once released, it goes. A filesystem without
project quotas (XFS or ext4) is refused, naming the option."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from cluster_tools import scratch_alloc as sa  # noqa: E402

DAY = sa.DAY


class Host(sa.Host):
    def __init__(self, mounts):
        self.mounts, self.commands, self.dirs, self.used, self.owners = mounts, [], set(), {}, {}

    def read(self, path):
        return self.mounts

    def xfs_quota(self, mount, command):
        self.commands.append(command)
        if command.startswith("quota -p"):
            pid = command.split()[-1]
            return f"/dev/md0 {self.used.get(pid, 0)} 0 0 00 [--------]\n"
        return ""

    def make_dir(self, path, uid, gid):
        self.dirs.add(path)
        self.owners[path] = uid

    def owner(self, path):
        return (self.owners.get(path, 0), True) if path in self.dirs else None

    def rename(self, src, dst):
        self.dirs.discard(src)
        self.dirs.add(dst)

    def remove_tree(self, path):
        self.dirs.discard(path)

    def exists(self, path):
        return path in self.dirs

    def user(self, name):
        return (1001, 1001) if name == "alice" else None

    def disk(self, mount):
        return 100 * 1024**4, 30 * 1024**4


XFS = "/dev/md0 /scratch xfs rw,noatime,attr2,inode64,prjquota 0 0\n"
CFG = {"mount": "/scratch", "max_bytes": "10T", "max_days": 180, "max_extend_days": 90, "grace_days": 14}


def fresh():
    return {"allocations": {}}


class ScratchAllocTests(unittest.TestCase):
    def test_create_makes_the_persons_directory_with_their_uid_as_the_project(self):
        host, data = Host(XFS), fresh()
        out = sa.create(host, CFG, data, "alice", "2T", 30, "training data", 0)
        self.assertEqual((out["path"], out["uid"]), ("/scratch/alice", 1001))
        self.assertEqual((out["bytes"], out["expires"]), (2 * 1024**4, 30 * DAY))
        self.assertIn("project -s -p /scratch/alice 1001", host.commands)
        self.assertIn(f"limit -p bsoft=0 bhard={2 * 1024**3}k 1001", host.commands)
        with self.assertRaisesRegex(sa.Error, "already"):
            sa.create(host, CFG, data, "alice", "1T", 30, "", 0)

    def test_their_own_directory_is_taken_as_it_is_and_someone_elses_refused(self):
        host = Host(XFS)
        host.dirs.add("/scratch/alice")
        host.owners["/scratch/alice"] = 1001
        out = sa.create(host, CFG, fresh(), "alice", "1T", 30, "", 0)
        self.assertEqual(out["path"], "/scratch/alice")
        host.owners["/scratch/alice"] = 1002
        with self.assertRaisesRegex(sa.Error, "not alice's own"):
            sa.create(host, CFG, fresh(), "alice", "1T", 30, "", 0)

    def test_requests_past_the_hard_caps_or_for_nobody_are_refused(self):
        for user, size, days in (
            ("bob", "1T", 1),
            ("alice", "11T", 1),
            ("alice", "1T", 181),
            ("alice", "x", 1),
        ):
            with self.subTest(user=user, size=size, days=days), self.assertRaises(sa.Error):
                sa.create(Host(XFS), CFG, fresh(), user, size, days, "", 0)

    def test_resize_extend_and_release(self):
        host, data = Host(XFS), fresh()
        sa.create(host, CFG, data, "alice", "1T", 30, "", 0)
        sa.resize(host, CFG, data, "alice", "3T")
        self.assertEqual(data["allocations"]["alice"]["bytes"], 3 * 1024**4)
        sa.extend(host, CFG, data, "alice", 90, 0)
        self.assertEqual(data["allocations"]["alice"]["expires"], 120 * DAY)
        with self.assertRaisesRegex(sa.Error, "1 to 90"):
            sa.extend(host, CFG, data, "alice", 91, 0)  # one extension adds at most 90 days
        sa.extend(host, CFG, data, "alice", 60, 0)  # to 180 days from when it was made
        with self.assertRaisesRegex(sa.Error, "180 days from when it was made"):
            sa.extend(host, CFG, data, "alice", 1, 0)
        sa.release(host, CFG, data, "alice", 5)
        self.assertEqual(data["allocations"]["alice"]["state"], "released")
        self.assertIn("/scratch/.released/alice-5", host.dirs)

    def test_sweep_freezes_then_removes_after_the_grace_period(self):
        host, data = Host(XFS), fresh()
        sa.create(host, CFG, data, "alice", "1T", 1, "", 0)
        host.used["1001"] = 5000
        self.assertEqual(sa.sweep(host, CFG, data, DAY - 1), [])
        lines = sa.sweep(host, CFG, data, DAY)
        self.assertEqual(data["allocations"]["alice"]["state"], "frozen")
        self.assertIn("limit -p bsoft=0 bhard=5000k 1001", host.commands)
        self.assertTrue(lines[0].startswith("froze alice"))
        # Extending a frozen allocation gives its size back.
        sa.extend(host, CFG, data, "alice", 1, DAY)
        self.assertEqual(data["allocations"]["alice"]["state"], "active")
        sa.sweep(host, CFG, data, 2 * DAY)
        lines = sa.sweep(host, CFG, data, 2 * DAY + 14 * DAY)
        self.assertEqual(data["allocations"], {})
        self.assertEqual(host.dirs, set())
        self.assertTrue(lines[-1].startswith("removed alice"))

    def test_the_ledger_adds_what_was_held_and_used_and_outlives_the_allocation(self):
        host, data = Host(XFS), {"allocations": {}, "ledger": {}}
        sa.create(host, CFG, data, "alice", "1T", 3, "", 0)
        host.used["1001"] = 1024**3  # 1 TiB used, in KiB
        sa.sweep(host, CFG, data, DAY)
        book = data["ledger"]["alice"]
        self.assertEqual(
            (book["allocated_byte_seconds"], book["used_byte_seconds"]), (1024**4 * DAY, 1024**4 * DAY)
        )
        sa.sweep(host, CFG, data, 2 * DAY)
        self.assertEqual(
            book["allocated_byte_seconds"], 2 * 1024**4 * DAY, "only the time since the last sweep"
        )
        sa.release(host, CFG, data, "alice", 2 * DAY)
        sa.sweep(host, CFG, data, 3 * DAY)
        self.assertEqual(data["allocations"], {})
        self.assertEqual(
            data["ledger"]["alice"]["allocated_byte_seconds"], 2 * 1024**4 * DAY, "kept, not grown"
        )
        self.assertIn("alice", sa.listing(host, CFG, data, "alice")["ledger"])

    def test_the_listing_carries_the_disk(self):
        host, data = Host(XFS), fresh()
        sa.create(host, CFG, data, "alice", "1T", 30, "", 0)
        listing = sa.listing(host, CFG, data, "alice")
        self.assertEqual([a["user"] for a in listing["allocations"]], ["alice"])
        self.assertEqual(listing["disk"], {"bytes": 100 * 1024**4, "used": 30 * 1024**4})
        self.assertEqual(sa.listing(host, CFG, data, "bob")["allocations"], [])

    def test_the_filesystem_must_have_project_quotas(self):
        sa.check_mount(Host(XFS), "/scratch")
        sa.check_mount(Host(XFS.replace(" xfs ", " ext4 ")), "/scratch")  # ext4 with prjquota too
        with self.assertRaisesRegex(sa.Error, "prjquota"):
            sa.check_mount(Host(XFS.replace("prjquota", "noquota")), "/scratch")
        with self.assertRaisesRegex(sa.Error, "ext4 and xfs"):
            sa.check_mount(Host(XFS.replace(" xfs ", " btrfs ")), "/scratch")
        with self.assertRaisesRegex(sa.Error, "not mounted"):
            sa.check_mount(Host(""), "/scratch")

    def test_the_registry_is_written_whole_under_its_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = sa.Registry(os.path.join(tmp, "r", "registry.json"))
            with reg.open() as data:
                data["allocations"]["alice"] = {"user": "alice"}
            self.assertEqual(
                json.loads(Path(reg.path).read_text())["allocations"], {"alice": {"user": "alice"}}
            )
            self.assertEqual(os.stat(reg.path).st_mode & 0o777, 0o600)

    def test_the_bot_key_may_ask_only_for_requests(self):
        self.assertEqual(
            sa.served("c.json", "create alice 2T 30"),
            ["scratch-alloc", "c.json", "create", "alice", "2T", "30"],
        )
        self.assertEqual(
            sa.served("c.json", "extend alice 7"), ["scratch-alloc", "c.json", "extend", "alice", "7"]
        )
        self.assertEqual(sa.served("c.json", "create alice 2T 30 'two words'")[-1], "two words")
        # A reason with spaces arrives quoted; unquoted it is too many words.
        for bad in (
            "sweep",
            "metrics",
            "rm -rf /",
            "extend alice",
            "",
            "list a b",
            "create alice 2T 30 my run",
        ):
            with self.subTest(request=bad), self.assertRaises(sa.Error):
                sa.served("c.json", bad)


if __name__ == "__main__":
    unittest.main()
