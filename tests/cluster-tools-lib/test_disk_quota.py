"""clustertools.disk_quota against a fake host: what it would run, and nothing twice.

A filesystem mounted with its quota options (host_baseline_mount_options writes them)
gets quotas on and every user's limits; one mounted without them is refused, naming
what to add; a rerun on a host that already matches does nothing; removal takes the
quotas back; the metrics carry each user's usage and limits. No real command runs.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from clustertools import disk_quota as dq  # noqa: E402
from clustertools import prom  # noqa: E402

FSTAB = "# /etc/fstab\nUUID=abc /               ext4    defaults,noatime 0 1\nUUID=def /boot/efi vfat umask=0077 0 1\n"
CSV_HEAD = "User,BlockStatus,FileStatus,BlockUsed,BlockSoftLimit,BlockHardLimit,BlockGrace,FileUsed,FileSoftLimit,FileHardLimit,FileGrace\n"
GB = 1024 * 1024  # KiB


class FakeHost:
    def __init__(
        self,
        fstab=FSTAB,
        live="rw,noatime,usrjquota=aquota.user,jqfmt=vfsv1",
        feature=False,
        quota_file=False,
        on=False,
        csv="",
        grace="7days",
    ):
        self.files = {"/etc/fstab": fstab, "/proc/self/mounts": f"/dev/sda4 / ext4 {live} 0 0\n"}
        self.feature, self.on, self.csv, self.grace = feature, on, csv, grace
        self.exist = {"/aquota.user"} if quota_file else set()
        self.ran, self.wrote, self.removed = [], {}, []

    def read(self, path):
        return self.files[path]

    def write(self, path, text):
        self.wrote[path] = text
        self.files[path] = text

    def exists(self, path):
        return path in self.exist

    def remove(self, path):
        self.removed.append(path)

    def run(self, argv, check=True):
        self.ran.append(argv)
        if argv[0] == "tune2fs":
            return (
                "Filesystem features:      has_journal ext_attr"
                + (" quota" if self.feature else "")
                + " extent\n"
            )
        if argv[:2] == ["quotaon", "-pu"]:
            return f"user quota on / (/dev/sda4) is {'on' if self.on else 'off'}\n"
        if argv[:2] == ["repquota", "-u"] and "csv" in argv:
            return CSV_HEAD + self.csv
        if argv[:2] == ["repquota", "-u"]:
            return f"*** Report for user quotas on device /dev/sda4\nBlock grace time: {self.grace}; Inode grace time: {self.grace}\n"
        return ""


def plan(host, cfg, action="apply"):
    return [text for text, _ in dq.steps_for(host, cfg, action)]


def run(host, cfg, action="apply"):
    for _, act in dq.steps_for(host, cfg, action):
        act()


CFG = {
    "filesystems": ["/"],
    "limits": {
        "alice": {"block_soft": "10G", "block_hard": "20G", "inode_soft": 200000, "inode_hard": "500k"}
    },
    "release": ["bob"],
    "grace_days": 7,
}


class Sizes(unittest.TestCase):
    def test_sizes_and_counts(self):
        self.assertEqual(dq.kib("20G"), 20 * GB)
        self.assertEqual(dq.kib("512M"), 512 * 1024)
        self.assertEqual(dq.kib(1024), 1024)
        self.assertEqual(dq.inodes("500k"), 500000)
        for bad in ("20GB", "-1", "1.5G", ""):
            with self.subTest(size=bad), self.assertRaises(dq.Error):
                dq.kib(bad)

    def test_a_soft_limit_above_its_hard_limit_is_refused(self):
        with self.assertRaises(dq.Error):
            dq.wanted({"a": {"block_soft": "30G", "block_hard": "20G"}})


class Parse(unittest.TestCase):
    def test_report_and_grace(self):
        csv = (
            CSV_HEAD
            + "root,ok,ok,1000,0,0,,10,0,0,\nalice,ok,ok,2048,10485760,20971520,,5,200000,500000,\n#1234,ok,ok,1,0,0,,1,0,0,\n"
        )
        got = dq.report(csv)
        self.assertEqual(got["alice"], (2048, 10485760, 20971520, 5, 200000, 500000))
        self.assertIn("1234", got)
        self.assertEqual(
            dq.grace_shown("Block grace time: 7days; Inode grace time: 7days\n"), ("7days", "7days")
        )

    def test_feature_and_mounts(self):
        self.assertTrue(dq.has_feature("Filesystem features:      has_journal quota extent\n"))
        self.assertFalse(dq.has_feature("Filesystem features:      has_journal extent\n"))
        self.assertEqual(
            dq.mounts("/dev/sda4 / ext4 rw,usrjquota=aquota.user 0 0\n")["/"][2][1], "usrjquota=aquota.user"
        )


class Apply(unittest.TestCase):
    def test_a_fresh_host_gets_quotacheck_quotaon_grace_and_limits(self):
        host = FakeHost()
        steps = plan(host, CFG)
        self.assertEqual(
            steps,
            [
                "quotacheck -cum /",
                "quotaon -u /",
                "setquota -t -u 604800 604800 /",
                f"setquota -u alice {10 * GB} {20 * GB} 200000 500000 /",
            ],
        )
        self.assertEqual(host.wrote, {}, "a plan changes nothing")

    def test_a_mount_without_its_quota_options_is_refused_naming_them(self):
        with self.assertRaisesRegex(
            dq.Error,
            "usrjquota=aquota.user,jqfmt=vfsv1.*host_baseline_mount_options.*playbooks/generic/host-baseline.yml",
        ):
            plan(FakeHost(live="rw,noatime"), CFG)
        with self.assertRaisesRegex(dq.Error, "without usrquota"):
            plan(FakeHost(live="rw,noatime,usrjquota=aquota.user,jqfmt=vfsv1", feature=True), CFG)

    def test_the_quota_feature_needs_no_quota_file(self):
        steps = plan(FakeHost(live="rw,usrquota", feature=True), CFG)
        self.assertNotIn("quotacheck -cum /", steps)
        self.assertIn("quotaon -u /", steps)

    def test_a_host_that_matches_needs_nothing(self):
        csv = f"alice,ok,ok,10,{10 * GB},{20 * GB},,1,200000,500000,\nbob,ok,ok,10,0,0,,1,0,0,\n"
        host = FakeHost(quota_file=True, on=True, csv=csv)
        self.assertEqual(plan(host, CFG), [])

    def test_changed_and_released_limits(self):
        csv = f"alice,ok,ok,10,{5 * GB},{20 * GB},,1,200000,500000,\nbob,ok,ok,10,{GB},{2 * GB},,1,0,0,\ncarol,ok,ok,10,{GB},{2 * GB},,1,0,0,\n"
        host = FakeHost(quota_file=True, on=True, csv=csv, grace="3days")
        self.assertEqual(
            plan(host, CFG),
            [
                "setquota -t -u 604800 604800 /",
                f"setquota -u alice {10 * GB} {20 * GB} 200000 500000 /",
                "setquota -u bob 0 0 0 0 /",
            ],
            "carol is no one's to release: limits set by hand stay",
        )

    def test_accounting_only_sets_no_limit(self):
        steps = plan(FakeHost(), {"filesystems": ["/"], "limits": {}, "release": ["alice"]})
        self.assertFalse([s for s in steps if s.startswith("setquota -u ")])
        self.assertIn("quotaon -u /", steps)

    def test_only_ext4_and_only_mounted(self):
        host = FakeHost()
        host.files["/proc/self/mounts"] = "/dev/sda4 / xfs rw 0 0\n"
        with self.assertRaises(dq.Error):
            plan(host, CFG)
        with self.assertRaises(dq.Error):
            plan(FakeHost(), {**CFG, "filesystems": ["/srv"]})

    def test_apply_runs_what_the_plan_said_and_never_writes_fstab(self):
        host = FakeHost()
        run(host, CFG)
        self.assertIn(["quotacheck", "-cum", "/"], host.ran)
        self.assertEqual(host.wrote, {})


class Remove(unittest.TestCase):
    def test_removal_releases_turns_off_and_removes_the_quota_file(self):
        csv = f"alice,ok,ok,10,{10 * GB},{20 * GB},,1,0,0,\n"
        host = FakeHost(quota_file=True, on=True, csv=csv)
        self.assertEqual(
            plan(host, CFG, "remove"),
            ["setquota -u alice 0 0 0 0 /", "quotaoff -u /", "remove /aquota.user"],
        )
        run(host, CFG, "remove")
        self.assertEqual(host.removed, ["/aquota.user"])
        self.assertEqual(host.wrote, {})

    def test_nothing_to_remove_where_there_were_none(self):
        self.assertEqual(plan(FakeHost(), CFG, "remove"), [])


class Metrics(unittest.TestCase):
    def test_usage_and_limits_per_user(self):
        csv = f"root,ok,ok,1000,0,0,,10,0,0,\nalice,ok,ok,2048,{10 * GB},{20 * GB},,5,200000,500000,\n"
        values = prom.parse(dq.metrics(FakeHost(csv=csv), {"filesystems": ["/"]}, 1700000000))
        self.assertEqual(
            values[prom.key("disk_quota_used_bytes", filesystem="/", user="alice")], str(2048 * 1024)
        )
        self.assertEqual(
            values[prom.key("disk_quota_limit_bytes", filesystem="/", kind="hard", user="alice")],
            str(20 * GB * 1024),
        )
        self.assertNotIn(prom.key("disk_quota_limit_bytes", filesystem="/", kind="hard", user="root"), values)
        self.assertEqual(values[prom.key("disk_quota_last_success_timestamp_seconds")], "1700000000")


class Cli(unittest.TestCase):
    def test_usage(self):
        for argv in (["x"], ["x", "c.json"], ["x", "c.json", "metrics", "--check"], ["x", "c.json", "nope"]):
            with self.subTest(argv=argv):
                self.assertEqual(dq.main(argv), 2)


if __name__ == "__main__":
    unittest.main()
