"""cluster_tools.scratch_space: files unused for stale_days are listed for their owner and
removed grace_days after they were first listed, unless used before; nothing is
followed through a link or across a filesystem; each person sees their list, files used
since shown as spared; a mass access-time refresh is counted for an administrator."""

import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from cluster_tools import prom  # noqa: E402
from cluster_tools import scratch_space as ss  # noqa: E402

DAY = 86400
NOW = 2_000_000_000
ME = os.getuid()


class Host(ss.Host):
    """The real filesystem, with paths that seem to be another filesystem."""

    def __init__(self, other=(), busy=frozenset(), members=None):
        self.other, self.busy, self.members = set(other), busy, members or {}

    def running_users(self):
        return self.busy  # None: Slurm unreadable

    def folder_members(self, folder):
        return self.members.get(os.path.basename(folder), set())  # None: unreadable

    def lstat(self, path):
        st = os.lstat(path)
        if any(path == o or path.startswith(o + os.sep) for o in self.other):
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev + 1, *st[3:]))
        return st


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "scratch")
        self.projects = os.path.join(self.root, "projects")
        os.makedirs(os.path.join(self.root, "alice"))
        self.cfg = {
            "root": self.root, "projects": self.projects, "people": {"alice": ME},
            "list_name": ".purge-list", "stale_days": 60, "grace_days": 14, "max_entries": 100000,
            "burst_files": 3, "burst_share": 0.5, "burst_window": 600, "cleanup_top": 5,
            "state": os.path.join(self.tmp.name, "state.json"), "textfile": os.path.join(self.tmp.name, "s.prom"),
        }  # fmt: skip
        self.said = []

    def tearDown(self):
        self.tmp.cleanup()

    def file(self, rel, used_days_ago, data=b"x", atime=None):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        t = NOW - used_days_ago * DAY
        os.utime(path, (atime if atime is not None else t, t))
        return path

    def run_at(self, now, host=None, check=False):
        ss.purge(host or Host(), self.cfg, now, check=check, say=self.said.append)

    def due(self):
        return json.loads(Path(self.cfg["state"]).read_text())["due"]


class Cleanup(Base):
    def test_the_folders_worth_cleaning_come_from_the_same_walk(self):
        self.file("alice/old-runs/out.bin", 200, b"x" * 100)
        self.file("alice/live/out.bin", 1)
        self.run_at(NOW)
        text = Path(self.cfg["textfile"]).read_text()
        self.assertIn(
            f'disk_cleanup_candidate_unused_days{{filesystem="{self.root}",path="{self.root}/alice/old-runs",user="alice"}} 200',
            text,
        )
        self.assertNotIn("/alice/live", text)

    def test_a_stale_file_is_listed_then_removed_after_the_grace_period(self):
        old = self.file("alice/run1/out.bin", 70, b"12345")
        fresh = self.file("alice/keep.txt", 3)
        self.run_at(NOW)
        self.assertEqual(list(self.due()), [old])
        self.assertEqual(self.due()[old]["due"], NOW + 14 * DAY, "the clock starts when it is first listed")
        self.assertTrue(os.path.exists(old))
        self.run_at(NOW + 13 * DAY)
        self.assertTrue(os.path.exists(old), "not before its date")
        self.assertEqual(self.due()[old]["due"], NOW + 14 * DAY, "the date is kept, not restarted")
        self.run_at(NOW + 14 * DAY)
        self.assertFalse(os.path.exists(old))
        self.assertFalse(os.path.exists(os.path.dirname(old)), "the directory it emptied goes")
        self.assertTrue(os.path.exists(fresh) and os.path.isdir(os.path.join(self.root, "alice")))
        values = prom.parse(Path(self.cfg["textfile"]).read_text())
        self.assertEqual(values[prom.key("scratch_space_purge_removed_files")], "1")

    def test_a_running_jobs_files_are_deferred_once_then_go(self):
        # Over NFS a job re-reading a cached file leaves its server atime old: while its
        # owner has a running job a due file is deferred, once, by stale_days.
        old = self.file("alice/code/train.py", 70)
        self.run_at(NOW)
        self.run_at(NOW + 14 * DAY, host=Host(busy={"alice"}))
        self.assertTrue(os.path.exists(old))
        self.assertEqual(self.due()[old]["due"], NOW + (14 + 60) * DAY)
        self.assertEqual(self.due()[old]["deferred"], NOW + 14 * DAY)
        values = prom.parse(Path(self.cfg["textfile"]).read_text())
        self.assertEqual(values[prom.key("scratch_space_purge_deferred_files", user="alice")], "1")
        listing = Path(self.root, "alice", ".purge-list").read_text()
        self.assertIn("deferred once because you had running jobs", listing)
        self.run_at(NOW + (14 + 60) * DAY, host=Host(busy={"alice"}))
        self.assertFalse(os.path.exists(old), "deferred once only: it goes though jobs still run")

    def test_a_job_still_configuring_counts_as_running(self):
        # vocab.job_holds, not a list of codes: CONFIGURING (nodes booting, prolog) holds
        # its allocation, and its job reads its files as soon as it starts.
        jobs = {"jobs": [{"user_name": "alice", "job_state": ["RUNNING", "CONFIGURING"]},
                         {"user_name": "bob", "job_state": ["PENDING"]}]}  # fmt: skip
        with mock.patch.object(ss.slurm, "load", return_value=jobs):
            self.assertEqual(ss.Host().running_users(), {"alice"})

    def test_a_collaboration_folders_file_waits_for_any_members_job(self):
        old = self.file("projects/vision/shared.bin", 70)
        self.run_at(NOW)
        self.run_at(NOW + 14 * DAY, host=Host(busy={"carol"}, members={"vision": {"alice", "carol"}}))
        self.assertTrue(os.path.exists(old), "a member's job may read a colleague's files")
        self.assertTrue(any("carol (a member of vision)" in s for s in self.said))
        unreadable = self.file("projects/audio/a.bin", 70)
        self.run_at(NOW + 15 * DAY, host=Host(busy={"dave"}, members={"audio": None}))
        self.assertTrue(os.path.exists(unreadable), "members unknown: deferred, not removed")

    def scanned(self, now):
        """The due map of one run, its dates come: the removal still to do."""
        files, *_, truncated = ss.scan(Host(), self.cfg, now)
        due = ss.plan(self.cfg, files, {"due": {}}, truncated, now)
        for entry in due.values():
            entry["due"] = now
        return due

    def test_a_directory_swapped_for_a_link_leads_nowhere(self):
        # Between the scan and the removal a person swaps a directory of theirs for a
        # link to another's tree: the removal walks without following links, so the
        # other's file stays (it went, with the path string followed as root).
        mine = self.file("alice/sub/x", 70)
        theirs = self.file("bob/data/x", 70)
        due = self.scanned(NOW)
        os.rename(os.path.dirname(mine), os.path.join(self.root, "alice", "sub.real"))
        os.symlink(os.path.dirname(theirs), os.path.dirname(mine))
        ss.remove(Host(), self.cfg, due, NOW, False, self.said.append)
        self.assertTrue(os.path.exists(theirs))
        self.assertTrue(os.path.exists(os.path.join(self.root, "alice", "sub.real", "x")))

    def test_the_walk_lists_nothing_through_a_swapped_directory(self):
        theirs = self.file("bob/data/x", 70)
        os.makedirs(os.path.join(self.root, "alice", "sub"))
        host = Host()
        host.root = self.root
        self.assertEqual([e.name for e in host.scandir(os.path.join(self.root, "alice"))], ["sub"])
        os.rmdir(os.path.join(self.root, "alice", "sub"))
        os.symlink(os.path.dirname(theirs), os.path.join(self.root, "alice", "sub"))
        with self.assertRaises(OSError):  # a link where the walk found a directory
            host.scandir(os.path.join(self.root, "alice", "sub"))

    def test_a_file_replaced_after_the_scan_stays(self):
        old = self.file("alice/data.bin", 70)
        due = self.scanned(NOW)
        new = self.file("alice/new.bin", 70, data=b"new")  # another inode, made to look old
        os.rename(new, old)
        ss.remove(Host(), self.cfg, due, NOW, False, self.said.append)
        self.assertTrue(os.path.exists(old))
        self.assertTrue(any("no longer the file that was listed" in s for s in self.said))

    def test_another_persons_jobs_defer_nothing(self):
        old = self.file("alice/data.bin", 70)
        self.run_at(NOW)
        self.run_at(NOW + 14 * DAY, host=Host(busy={"bob"}))
        self.assertFalse(os.path.exists(old))

    def test_nothing_goes_when_slurm_cannot_be_read(self):
        old = self.file("alice/data.bin", 70)
        self.run_at(NOW)
        self.run_at(NOW + 14 * DAY, host=Host(busy=None))
        self.assertTrue(os.path.exists(old))
        self.assertEqual(self.due()[old]["due"], NOW + 14 * DAY, "its date is kept for the next run")
        self.assertTrue(any("nothing is removed" in s for s in self.said))

    def test_a_file_used_in_the_grace_period_is_spared(self):
        old = self.file("alice/data.bin", 70)
        self.run_at(NOW)
        os.utime(old, (NOW + 5 * DAY, os.lstat(old).st_mtime))  # read on day 5
        self.run_at(NOW + 6 * DAY)
        self.assertEqual(self.due(), {})
        self.run_at(NOW + 15 * DAY)
        self.assertTrue(os.path.exists(old))
        self.assertFalse(
            os.path.exists(os.path.join(self.root, "alice", ".purge-list")), "nothing due: no list"
        )

    def test_check_mode_changes_nothing(self):
        old = self.file("alice/a", 70)
        self.run_at(NOW)
        self.run_at(NOW + 15 * DAY, check=True)
        self.assertTrue(os.path.exists(old))
        self.assertIn(f"remove {old}", self.said)

    def test_links_are_never_followed_and_other_filesystems_never_entered(self):
        outside = os.path.join(self.tmp.name, "outside")
        os.makedirs(outside)
        target = os.path.join(outside, "precious")
        Path(target).write_bytes(b"x")
        os.utime(target, (NOW - 90 * DAY, NOW - 90 * DAY))
        link = os.path.join(self.root, "alice", "link")
        os.symlink(outside, link)
        os.utime(link, (NOW - 90 * DAY, NOW - 90 * DAY), follow_symlinks=False)
        mounted = self.file("alice/mnt/file", 90)
        host = Host(other=[os.path.join(self.root, "alice", "mnt")])
        self.run_at(NOW, host)
        self.run_at(NOW + 15 * DAY, host)
        self.assertTrue(os.path.exists(target), "never through a link")
        self.assertFalse(os.path.lexists(link), "the stale link itself goes")
        self.assertTrue(os.path.exists(mounted), "another filesystem is not entered")

    def test_names_with_any_bytes(self):
        name = os.fsdecode(b"alice/odd\tname\nwith\xff")
        old = self.file(name, 70)
        self.run_at(NOW)
        text, _ = ss.show(Host(), self.root, "alice", NOW)
        self.assertIn(ss.escape(old), text)
        self.run_at(NOW + 15 * DAY)
        self.assertFalse(os.path.lexists(old))

    def test_the_list_file_and_the_persons_directory_stay(self):
        self.file("alice/a", 70)
        self.run_at(NOW)
        listing = os.path.join(self.root, "alice", ".purge-list")
        os.utime(listing, (NOW - 90 * DAY, NOW - 90 * DAY))
        self.run_at(NOW + 15 * DAY)
        self.assertTrue(os.path.isdir(os.path.join(self.root, "alice")))
        self.assertNotIn(listing, self.due())

    def test_a_list_file_link_is_replaced_never_written_through(self):
        self.file("alice/a", 70)
        victim = os.path.join(self.tmp.name, "victim")
        Path(victim).write_text("keep")
        os.symlink(victim, os.path.join(self.root, "alice", ".purge-list"))
        self.run_at(NOW)
        self.assertEqual(Path(victim).read_text(), "keep")
        self.assertFalse(os.path.islink(os.path.join(self.root, "alice", ".purge-list")))

    def test_collaboration_folders_count_to_each_files_owner(self):
        shared = self.file("projects/team/data.bin", 70)
        self.run_at(NOW)
        self.assertEqual(self.due()[shared]["user"], "alice")
        self.run_at(NOW + 15 * DAY)
        self.assertFalse(os.path.exists(shared))
        self.assertTrue(os.path.isdir(os.path.join(self.projects, "team")), "the folder itself stays")

    def test_a_truncated_walk_keeps_the_dates_it_did_not_reach(self):
        old = self.file("alice/a", 70)
        self.run_at(NOW)
        self.cfg["max_entries"] = 0
        self.run_at(NOW + DAY)
        self.assertIn(old, self.due())


class Burst(Base):
    def test_old_files_all_used_at_once_are_counted(self):
        for i in range(4):
            self.file(f"alice/old{i}", 100, atime=NOW - 60)  # unchanged for 100 days, read a minute ago
        self.file("alice/new", 1)
        self.run_at(NOW)
        values = prom.parse(Path(self.cfg["textfile"]).read_text())
        self.assertEqual(values[prom.key("scratch_space_purge_burst_files", user="alice")], "4")

    def test_ordinary_use_is_not(self):
        for i in range(4):
            self.file(f"alice/f{i}", 1)
        self.run_at(NOW)
        values = prom.parse(Path(self.cfg["textfile"]).read_text())
        self.assertNotIn(prom.key("scratch_space_purge_burst_files", user="alice"), values)


class Show(Base):
    def test_a_person_sees_what_goes_and_what_was_used_since(self):
        a = self.file("alice/a", 70, b"x" * 2048)
        b = self.file("alice/b", 80)
        self.run_at(NOW)
        os.utime(b, (NOW + DAY, os.lstat(b).st_mtime))
        text, status = ss.show(Host(), self.root, "alice", NOW + DAY)
        self.assertEqual(status, 0)
        self.assertIn("1 due", text)
        self.assertIn("1 spared", text)
        self.assertRegex(text, r"goes \d{4}-\d\d-\d\d +2K  " + a)
        self.assertIn("spared (used", text)

    def test_nothing_due(self):
        text, _ = ss.show(Host(), self.root, "alice", NOW)
        self.assertIn("Nothing of yours", text)

    def test_the_metrics_name_each_persons_files_and_date(self):
        self.file("alice/a", 70, b"abc")
        self.run_at(NOW)
        values = prom.parse(Path(self.cfg["textfile"]).read_text())
        self.assertEqual(values[prom.key("scratch_space_purge_due_files", user="alice")], "1")
        self.assertEqual(values[prom.key("scratch_space_purge_due_bytes", user="alice")], "3")
        self.assertEqual(
            values[prom.key("scratch_space_purge_next_timestamp_seconds", user="alice")], str(NOW + 14 * DAY)
        )
        self.assertIn(prom.key("scratch_space_last_success_timestamp_seconds"), values)


class Cli(Base):
    def test_usage(self):
        with self.assertRaises(SystemExit) as e, mock.patch("sys.stderr"):
            ss.main(["bogus", "args", "x", "y"])
        self.assertEqual(e.exception.code, 2)

    def test_a_person_runs_it_without_a_config(self):
        self.file("alice/a", 70)
        self.run_at(NOW)
        out = io.StringIO()
        with (
            mock.patch.object(ss.pwd, "getpwuid", lambda uid: mock.Mock(pw_name="alice")),
            mock.patch("sys.stdout", out),
        ):
            self.assertEqual(ss.main(["--root", self.root]), 0)
        self.assertIn("alice/a", out.getvalue())

    def test_config_is_checked(self):
        path = os.path.join(self.tmp.name, "c.json")
        Path(path).write_text(json.dumps({**self.cfg, "stale_days": 0}))
        with self.assertRaises(ss.config.ConfigError):
            ss.load_config(path)
        Path(path).write_text(json.dumps(self.cfg))
        self.assertEqual(ss.load_config(path)["grace_days"], 14)


if __name__ == "__main__":
    unittest.main()
