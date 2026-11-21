"""clustertools.scratch_alloc: a request becomes a directory of its own with a project
quota at once; resize, extend and release act on the registry and the quota; at the end
date the allocation freezes at what it holds, and after the grace period, or once
released, it goes. A filesystem without project quotas (XFS or ext4) is refused, naming the option."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from clustertools import scratch_alloc as sa  # noqa: E402

DAY = sa.DAY


class Host(sa.Host):
    def __init__(self, mounts):
        self.mounts, self.commands, self.dirs, self.used = mounts, [], set(), {}

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

    def rename(self, src, dst):
        self.dirs.discard(src)
        self.dirs.add(dst)

    def remove_tree(self, path):
        self.dirs.discard(path)

    def exists(self, path):
        return path in self.dirs

    def user(self, name):
        return (1001, 1001) if name == "alice" else None


XFS = "/dev/md0 /scratch xfs rw,noatime,attr2,inode64,prjquota 0 0\n"
CFG = {"mount": "/scratch", "max_bytes": "20T", "max_days": 180, "grace_days": 14}


def fresh():
    return {"next_id": 1000, "allocations": {}}


class ScratchAllocTests(unittest.TestCase):
    def test_create_makes_a_directory_with_its_quota(self):
        host, data = Host(XFS), fresh()
        out = sa.create(host, CFG, data, "alice", "2T", 30, "training data", 0)
        self.assertEqual(out["path"], "/scratch/alice-1000")
        self.assertEqual(out["bytes"], 2 * 1024**4)
        self.assertEqual(out["expires"], 30 * DAY)
        self.assertIn("project -s -p /scratch/alice-1000 1000", host.commands)
        self.assertIn(f"limit -p bsoft=0 bhard={2 * 1024**3}k 1000", host.commands)
        self.assertEqual(data["next_id"], 1001)

    def test_requests_past_the_hard_caps_or_for_nobody_are_refused(self):
        host = Host(XFS)
        for user, size, days in (
            ("bob", "1T", 1),
            ("alice", "21T", 1),
            ("alice", "1T", 181),
            ("alice", "x", 1),
        ):
            with self.subTest(user=user, size=size, days=days), self.assertRaises(sa.Error):
                sa.create(host, CFG, fresh(), user, size, days, "", 0)

    def test_resize_extend_and_release(self):
        host, data = Host(XFS), fresh()
        sa.create(host, CFG, data, "alice", "1T", 30, "", 0)
        sa.resize(host, CFG, data, 1000, "3T")
        self.assertEqual(data["allocations"]["1000"]["bytes"], 3 * 1024**4)
        sa.extend(host, CFG, data, 1000, 14, 0)
        self.assertEqual(data["allocations"]["1000"]["expires"], 44 * DAY)
        with self.assertRaises(sa.Error):
            sa.extend(host, CFG, data, 1000, 170, 0)  # past max_days from today
        sa.release(host, CFG, data, 1000, 5)
        self.assertEqual(data["allocations"]["1000"]["state"], "released")
        self.assertIn("/scratch/.released/1000", host.dirs)

    def test_sweep_freezes_then_removes_after_the_grace_period(self):
        host, data = Host(XFS), fresh()
        sa.create(host, CFG, data, "alice", "1T", 1, "", 0)
        host.used["1000"] = 5000
        self.assertEqual(sa.sweep(host, CFG, data, DAY - 1), [])
        lines = sa.sweep(host, CFG, data, DAY)
        self.assertEqual(data["allocations"]["1000"]["state"], "frozen")
        self.assertIn("limit -p bsoft=0 bhard=5000k 1000", host.commands)
        self.assertTrue(lines[0].startswith("froze 1000"))
        # Extending a frozen allocation gives its size back.
        sa.extend(host, CFG, data, 1000, 1, DAY)
        self.assertEqual(data["allocations"]["1000"]["state"], "active")
        sa.sweep(host, CFG, data, 2 * DAY)
        lines = sa.sweep(host, CFG, data, 2 * DAY + 14 * DAY)
        self.assertEqual(data["allocations"], {})
        self.assertEqual(host.dirs, set())
        self.assertTrue(lines[-1].startswith("removed 1000"))

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
            reg = sa.Registry(os.path.join(tmp, "r", "registry.json"), 1000)
            with reg.open() as data:
                data["next_id"] = 1005
            self.assertEqual(json.loads(Path(reg.path).read_text())["next_id"], 1005)
            self.assertEqual(os.stat(reg.path).st_mode & 0o777, 0o600)

    def test_the_bot_key_may_ask_only_for_requests(self):
        self.assertEqual(
            sa.served("c.json", "create alice 2T 30"),
            ["scratch-alloc", "c.json", "create", "alice", "2T", "30"],
        )
        self.assertEqual(
            sa.served("c.json", "extend 1000 7"), ["scratch-alloc", "c.json", "extend", "1000", "7"]
        )
        self.assertEqual(sa.served("c.json", "create alice 2T 30 'two words'")[-1], "two words")
        # A reason with spaces arrives quoted; unquoted it is too many words.
        for bad in (
            "sweep",
            "metrics",
            "rm -rf /",
            "extend 1000",
            "",
            "list a b",
            "create alice 2T 30 my run",
        ):
            with self.subTest(request=bad), self.assertRaises(sa.Error):
                sa.served("c.json", bad)


if __name__ == "__main__":
    unittest.main()
