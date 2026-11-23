"""cluster_tools.disk_quota against a fake host: what it would run, and nothing twice.

A filesystem mounted with its quota options (host_baseline_mount_options writes them)
gets quotas on and every user's hard limits; the warn level never reaches the kernel
(soft stays 0: no grace period that later refuses writes), it is only exported. One
mounted without the options is refused, naming what to add. Project quotas (a tree's
total) work on ext4 and XFS, as deploy-time projects and as a library. A rerun on a host
that already matches does nothing; removal takes the quotas back. No real command runs.
"""

import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from cluster_tools import disk_quota as dq  # noqa: E402
from cluster_tools import prom  # noqa: E402

CSV_HEAD = "User,BlockStatus,FileStatus,BlockUsed,BlockSoftLimit,BlockHardLimit,BlockGrace,FileUsed,FileSoftLimit,FileHardLimit,FileGrace\n"
PCSV_HEAD = CSV_HEAD.replace("User,", "Project,", 1)
GB = 1024 * 1024  # KiB
TB = 1024 * GB


class FakeHost:
    def __init__(self, live="rw,noatime,usrjquota=aquota.user,jqfmt=vfsv1", feature=False, quota_file=False, on=False,
                 csv="", home=None, project_ids=None, pcsv="", xfs_report="", pon=True):  # fmt: skip
        table = f"/dev/sda4 / ext4 {live} 0 0\n"
        if home:
            table += f"/dev/sdb1 /home ext4 {home} 0 0\n"
        self.files = {"/proc/self/mounts": table}
        self.feature, self.on, self.csv, self.pcsv, self.xfs_report, self.pon = (
            feature,
            on,
            csv,
            pcsv,
            xfs_report,
            pon,
        )
        self.project_ids = dict(project_ids or {})
        self.exist = {"/aquota.user"} if quota_file else set()
        self.ran, self.removed, self.scanned = [], [], []

    def read(self, path):
        return self.files[path]

    def exists(self, path):
        return path in self.exist

    def remove(self, path):
        self.removed.append(path)

    def xfs_quota(self, mount, cmd):
        self.ran.append(["xfs_quota", "-x", "-c", cmd, mount])
        return self.xfs_report if cmd.startswith("quota ") else ""

    def scan(self, argv):
        self.scanned.append(argv)
        return self.run(argv)

    def run(self, argv, check=True, timeout=None):
        self.ran.append(argv)
        if argv[0] == "tune2fs":
            return (
                "Filesystem features:      has_journal ext_attr"
                + (" quota" if self.feature else "")
                + " extent\n"
            )
        if argv[:2] == ["quotaon", "-pu"]:
            return f"user quota on / (/dev/sda4) is {'on' if self.on else 'off'}\n"
        if argv[:2] == ["quotaon", "-pP"]:
            return f"project quota on /home (/dev/sdb1) is {'on' if self.pon else 'off'}\n"
        if argv[:2] == ["repquota", "-P"]:
            return PCSV_HEAD + self.pcsv
        if argv[:2] == ["repquota", "-u"]:
            return CSV_HEAD + self.csv
        if argv[:2] == ["lsattr", "-pd"]:
            return f"{self.project_ids.get(argv[2], 0)} --------------e------- {argv[2]}\n"
        return ""


def plan(host, cfg, action="apply"):
    return [text for text, _ in dq.steps_for(host, cfg, action)]


def run(host, cfg, action="apply"):
    for _, act in dq.steps_for(host, cfg, action):
        act()


ALICE = {"block_hard": "20G", "inode_hard": "500k"}
LEVELS = {"warn_fraction": 0.9, "inodes_per_tib": 0}
CFG = {"filesystems": {"/": {"alice": ALICE}}, "release": ["alice", "bob"], **LEVELS}
PROJECT = {"/home/shared/datasets": {"filesystem": "/home", "id": 101, "block_hard": "20T"}}


class Sizes(unittest.TestCase):
    def test_sizes_and_counts(self):
        self.assertEqual(dq.kib("20G"), 20 * GB)
        self.assertEqual(dq.kib("512M"), 512 * 1024)
        self.assertEqual(dq.kib(1024), 1024)
        self.assertEqual(dq.inodes("500k"), 500000)
        for bad in ("20GB", "-1", "1.5G", ""):
            with self.subTest(size=bad), self.assertRaises(dq.Error):
                dq.kib(bad)

    def test_a_warn_level_of_its_own_is_refused(self):
        with self.assertRaisesRegex(dq.Error, "warn_fraction"):
            dq.wanted({"a": {"block_warn": "10G", "block_hard": "20G"}}, LEVELS)

    def test_each_warn_level_is_the_fraction_of_its_hard_limit(self):
        self.assertEqual(dq.levels("a", ALICE, LEVELS), (int(20 * GB * 0.9), 20 * GB, 450000, 500000))

    def test_the_file_count_follows_the_space_unless_named(self):
        ratio = {**LEVELS, "inodes_per_tib": 3000000}
        self.assertEqual(dq.levels("a", {"block_hard": "10T"}, ratio)[2:], (27000000, 30000000))
        self.assertEqual(dq.levels("a", {"block_hard": "5T"}, ratio)[3], 15000000)
        self.assertEqual(dq.levels("a", ALICE, ratio)[3], 500000, "an inode_hard of its own wins")
        self.assertEqual(dq.levels("a", {"block_hard": "10T"}, LEVELS)[3], 0, "0 per TiB: no file limit")

    def test_the_kernel_gets_the_hard_limits_only(self):
        self.assertEqual(dq.kernel(dq.levels("a", ALICE, LEVELS)), (0, 20 * GB, 0, 500000))


class Parse(unittest.TestCase):
    def test_report(self):
        csv = (
            CSV_HEAD
            + "root,ok,ok,1000,0,0,,10,0,0,\nalice,ok,ok,2048,0,20971520,,5,0,500000,\n#1234,ok,ok,1,0,0,,1,0,0,\n"
        )
        got = dq.report(csv)
        self.assertEqual(got["alice"], (2048, 0, 20971520, 5, 0, 500000))
        self.assertIn("1234", got)
        self.assertEqual(dq.report(PCSV_HEAD + "#101,ok,ok,5,0,9,,1,0,0,\n")["101"][:3], (5, 0, 9))

    def test_feature_and_mounts(self):
        self.assertTrue(dq.has_feature("Filesystem features:      has_journal quota extent\n"))
        self.assertFalse(dq.has_feature("Filesystem features:      has_journal extent\n"))
        self.assertEqual(
            dq.mounts("/dev/sda4 / ext4 rw,usrjquota=aquota.user 0 0\n")["/"][2][1], "usrjquota=aquota.user"
        )


class Apply(unittest.TestCase):
    def test_a_fresh_host_gets_quotacheck_quotaon_and_hard_limits(self):
        host = FakeHost()
        self.assertEqual(
            plan(host, CFG),
            ["quotacheck -cum /", "quotaon -u /", f"setquota -u alice 0 {20 * GB} 0 500000 /"],
        )
        self.assertFalse([a for a in host.ran if a[:2] == ["setquota", "-t"]], "no grace period is ever set")
        run(host, CFG)
        self.assertEqual(host.scanned, [["quotacheck", "-cum", "/"]], "the whole-filesystem read")

    def test_a_scan_runs_at_low_priority_for_up_to_a_day(self):
        with mock.patch.object(dq.proc, "run") as run:
            run.return_value.stdout = ""
            dq.Host().scan(["quotacheck", "-cum", "/home"])
        argv, kwargs = run.call_args
        self.assertEqual(argv[0], dq.LOW + ["quotacheck", "-cum", "/home"])
        self.assertNotIn("3", argv[0][argv[0].index("-c") + 1], "never the idle class: it can starve")
        self.assertEqual(kwargs["timeout"], dq.SCAN_TIMEOUT)

    def test_an_old_count_is_redone_before_quotas_go_on(self):
        # A quota file from an earlier quotacheck, quotas off since: writes in between are
        # not in it, so it is counted again before quotaon; once on, nothing is redone.
        steps = plan(FakeHost(quota_file=True, on=False), {"filesystems": {"/": {}}, "release": [], **LEVELS})
        self.assertEqual(steps, ["quotacheck -cum /", "quotaon -u /"])
        self.assertEqual(
            plan(FakeHost(quota_file=True, on=True), {"filesystems": {"/": {}}, "release": [], **LEVELS}), []
        )

    def test_a_mount_without_its_quota_options_is_refused_naming_them(self):
        with self.assertRaisesRegex(
            dq.Error,
            "usrjquota=aquota.user,jqfmt=vfsv1.*host_baseline_mount_options.*playbooks/generic/host-baseline.yml",
        ):
            plan(FakeHost(live="rw,noatime"), CFG)
        with self.assertRaisesRegex(dq.Error, "without usrquota"):
            plan(FakeHost(feature=True), CFG)

    def test_the_quota_feature_needs_no_quota_file(self):
        steps = plan(FakeHost(live="rw,usrquota", feature=True), CFG)
        self.assertNotIn("quotacheck -cum /", steps)
        self.assertIn("quotaon -u /", steps)

    def test_a_host_that_matches_needs_nothing(self):
        csv = f"alice,ok,ok,10,0,{20 * GB},,1,0,500000,\nbob,ok,ok,10,0,0,,1,0,0,\n"
        self.assertEqual(plan(FakeHost(quota_file=True, on=True, csv=csv), CFG), [])

    def test_a_soft_limit_left_from_before_goes_and_released_users_lose_theirs(self):
        csv = (
            f"alice,ok,ok,10,{10 * GB},{20 * GB},,1,200000,500000,\n"
            f"bob,ok,ok,10,{GB},{2 * GB},,1,0,0,\ncarol,ok,ok,10,{GB},{2 * GB},,1,0,0,\n"
        )
        self.assertEqual(
            plan(FakeHost(quota_file=True, on=True, csv=csv), CFG),
            [f"setquota -u alice 0 {20 * GB} 0 500000 /", "setquota -u bob 0 0 0 0 /"],
            "carol is no one's to release: limits set by hand stay",
        )

    def test_accounting_only_sets_no_limit(self):
        steps = plan(FakeHost(), {"filesystems": {"/": {}}, "release": ["alice"], **LEVELS})
        self.assertFalse([s for s in steps if s.startswith("setquota -u ")])
        self.assertIn("quotaon -u /", steps)

    def test_only_ext4_and_xfs_and_only_mounted(self):
        host = FakeHost()
        host.files["/proc/self/mounts"] = "/dev/sda4 / btrfs rw 0 0\n"
        with self.assertRaises(dq.Error):
            plan(host, CFG)
        with self.assertRaises(dq.Error):
            plan(FakeHost(), {**CFG, "filesystems": {"/srv": {}}})

    def test_xfs_quotas_come_with_the_mount_only_limits_are_set(self):
        host = FakeHost()
        host.files["/proc/self/mounts"] = "/dev/md0 / xfs rw,noatime 0 0\n"
        with self.assertRaisesRegex(dq.Error, "usrquota"):
            plan(host, CFG)
        host.files["/proc/self/mounts"] = "/dev/md0 / xfs rw,relatime,usrquota 0 0\n"
        self.assertEqual(plan(host, CFG), [f"setquota -u alice 0 {20 * GB} 0 500000 /"])
        csv = f"alice,ok,ok,10,0,{20 * GB},,1,0,0,\n"
        host.csv = csv
        self.assertEqual(plan(host, CFG, "remove"), ["setquota -u alice 0 0 0 0 /"], "no quotaoff, no file")

    def test_each_filesystem_its_own_limits(self):
        host = FakeHost(quota_file=True, on=True, home="rw,usrjquota=aquota.user,jqfmt=vfsv1")
        host.exist.add("/home/aquota.user")
        cfg = {
            "filesystems": {"/": {}, "/home": {"alice": {"block_hard": "10T"}}},
            "release": ["alice"],
            **LEVELS,
        }
        self.assertEqual(plan(host, cfg), [f"setquota -u alice 0 {10 * TB} 0 0 /home"])


class Projects(unittest.TestCase):
    def test_an_ext4_project_gets_its_tree_and_hard_limit(self):
        host = FakeHost(home="rw,prjquota", pon=False)
        self.assertEqual(
            plan(host, {"projects": PROJECT, **LEVELS}),
            [
                "quotaon -P /home",
                "make /home/shared/datasets project 101",
                f"limit project 101 (/home/shared/datasets) to {20 * TB} KiB",
            ],
        )
        run(host, {"projects": PROJECT, **LEVELS})
        self.assertIn(["chattr", "-R", "-p", "101", "+P", "/home/shared/datasets"], host.scanned)
        self.assertIn(["setquota", "-P", "101", "0", str(20 * TB), "0", "0", "/home"], host.ran)

    def test_a_project_that_matches_needs_nothing(self):
        host = FakeHost(
            home="rw,prjquota",
            project_ids={"/home/shared/datasets": 101},
            pcsv=f"#101,ok,ok,5,0,{20 * TB},,1,0,0,\n",
        )
        self.assertEqual(plan(host, {"projects": PROJECT, **LEVELS}), [])

    def test_a_filesystem_without_project_quotas_is_refused(self):
        with self.assertRaisesRegex(dq.Error, "without prjquota"):
            plan(FakeHost(home="rw,usrquota"), {"projects": PROJECT, **LEVELS})

    def test_xfs_projects_go_through_xfs_quota(self):
        host = FakeHost()
        host.files["/proc/self/mounts"] = "/dev/sda4 / ext4 rw 0 0\n/dev/sdc1 /scratch xfs rw,pquota 0 0\n"
        dq.project_set(host, "/scratch", "/scratch/alice-1", 1000)
        dq.project_limit(host, "/scratch", 1000, 5 * 1024**4, warn_bytes=4 * 1024**4)
        self.assertEqual(
            [a[3] for a in host.ran if a[0] == "xfs_quota"],
            ["project -s -p /scratch/alice-1 1000", f"limit -p bsoft=0 bhard={5 * TB}k 1000"],
            "the warn level never reaches the kernel",
        )
        host.xfs_report = f"/dev/sdc1 2048 0 {5 * TB} 00 [--------] /scratch\n"
        self.assertEqual(dq.project_used(host, "/scratch", 1000), 2048 * 1024)

    def test_removal_lifts_a_project_limit_and_keeps_its_tree(self):
        host = FakeHost(
            home="rw,prjquota",
            project_ids={"/home/shared/datasets": 101},
            pcsv=f"#101,ok,ok,5,0,{20 * TB},,1,0,0,\n",
        )
        self.assertEqual(
            plan(host, {"projects": PROJECT}, "remove"),
            ["lift the limit of project 101 (/home/shared/datasets)"],
        )
        run(host, {"projects": PROJECT}, "remove")
        self.assertIn(["setquota", "-P", "101", "0", "0", "0", "0", "/home"], host.ran)
        self.assertFalse(host.scanned)


class Remove(unittest.TestCase):
    def test_removal_releases_turns_off_and_removes_the_quota_file(self):
        csv = f"alice,ok,ok,10,0,{20 * GB},,1,0,0,\n"
        host = FakeHost(quota_file=True, on=True, csv=csv)
        self.assertEqual(
            plan(host, CFG, "remove"), ["setquota -u alice 0 0 0 0 /", "quotaoff -u /", "remove /aquota.user"]
        )
        run(host, CFG, "remove")
        self.assertEqual(host.removed, ["/aquota.user"])

    def test_nothing_to_remove_where_there_were_none(self):
        self.assertEqual(plan(FakeHost(), CFG, "remove"), [])


class Metrics(unittest.TestCase):
    def test_usage_hard_from_the_kernel_warn_from_the_configuration(self):
        csv = f"root,ok,ok,1000,0,0,,10,0,0,\nalice,ok,ok,2048,0,{20 * GB},,5,0,500000,\n"
        host = FakeHost(csv=csv, home="rw,prjquota", pcsv=f"#101,ok,ok,7,0,{20 * TB},,1,0,0,\n")
        values = prom.parse(dq.metrics(host, {**CFG, "projects": PROJECT}, 1700000000))

        def get(name, **labels):
            return values.get(prom.key(name, **labels))

        self.assertEqual(get("disk_quota_used_bytes", filesystem="/", user="alice"), str(2048 * 1024))
        self.assertEqual(
            get("disk_quota_limit_bytes", filesystem="/", kind="hard", user="alice"), str(20 * GB * 1024)
        )
        self.assertEqual(
            get("disk_quota_limit_bytes", filesystem="/", kind="warn", user="alice"),
            str(int(20 * GB * 0.9) * 1024),
        )
        self.assertIsNone(get("disk_quota_limit_bytes", filesystem="/", kind="hard", user="root"))
        labels = {"filesystem": "/home", "project": "/home/shared/datasets", "project_id": "101"}
        self.assertEqual(get("disk_quota_project_used_bytes", **labels), str(7 * 1024))
        self.assertEqual(
            get("disk_quota_project_limit_bytes", kind="warn", **labels), str(int(20 * TB * 0.9) * 1024)
        )
        self.assertEqual(get("disk_quota_last_success_timestamp_seconds"), "1700000000")


class Cli(unittest.TestCase):
    def test_usage(self):
        for argv in (["x"], ["x", "c.json"], ["x", "c.json", "metrics", "--check"], ["x", "c.json", "nope"]):
            with self.subTest(argv=argv):
                self.assertEqual(dq.main(argv), 2)


if __name__ == "__main__":
    unittest.main()


class Scan(unittest.TestCase):
    """The weekly scan weighs each base's folders (cluster_tools.cleanup) and writes them."""

    def test_old_large_folders_are_named_recent_ones_never(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            now = 2_000_000_000
            for rel, days in (("alice/old/a.bin", 200), ("alice/live/b.bin", 2), ("bob/stuff/c.bin", 90)):
                path = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(b"x" * 10)
                os.utime(path, (now - days * 86400, now - days * 86400))

            class Real(dq.Host):
                def owner(self, uid):
                    return "someone"

            cfg = {
                "scan": {tmp: [os.path.join(tmp, "*")]},
                "scan_top": 5,
                "scan_max_entries": 1000,
                "stale_days": 60,
            }
            text = prom.render(dq.scan(Real(), cfg, now))
            named = [line for line in text.splitlines() if line.startswith("disk_cleanup_candidate_bytes")]
            self.assertEqual(len(named), 2)
            self.assertTrue(any("/alice/old" in n for n in named) and any("/bob/stuff" in n for n in named))
            self.assertNotIn("/alice/live", text)
