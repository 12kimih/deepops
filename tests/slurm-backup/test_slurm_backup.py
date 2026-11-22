"""cluster_tools.slurm_backup: retention, metrics, and full runs against stub tools."""

import gzip
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from sandbox_path import sandbox_path  # noqa: E402

# What slurm_backup runs besides the database clients the tests fake.
TOOLS = ("sh", "bash", "gzip", "cat", "ls", "rm", "mkdir", "mv", "sha256sum", "timeout")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles" / "cluster_tools_lib" / "files"))
from cluster_tools import prom  # noqa: E402
from cluster_tools import slurm_backup as sb  # noqa: E402


class Pure(unittest.TestCase):
    def test_to_remove_keeps_the_newest(self):
        names = ["2026-11-01", "2026-11-03", "2026-11-02", "notes", "2026-10-31"]
        self.assertEqual(sb.to_remove(names, 2), ["2026-10-31", "2026-11-01"])
        self.assertEqual(sb.to_remove(names, 10), [])

    def test_to_remove_takes_partial_sets_left_by_an_interrupted_run(self):
        names = ["2026-11-02", ".2026-11-01.partial", "2026-11-03"]
        self.assertEqual(sb.to_remove(names, 5), [".2026-11-01.partial"])

    def test_problems_in_a_configuration(self):
        good = {
            "database": "d",
            "state_dir": "/s",
            "metrics_file": "/m",
            "textfile_dir": "/t",
            "textfile": "x.prom",
            "work_dir": "/w",
            "destinations": [{"path": "/a"}, {"path": "/b", "daily": 3, "weekly": 1}],
        }
        self.assertEqual(sb.problems_in(good), [])
        for change, word in (
            ({"destinations": []}, "no destinations"),
            ({"destinations": [{"path": "/a", "daily": 0}]}, "daily"),
            ({"destinations": [{"path": "/a", "weekly": True}]}, "weekly"),
            ({"destinations": [{"path": "a"}]}, "absolute"),
            ({"destinations": [{"path": "/a"}, {"path": "/a"}]}, "twice"),
            ({"weekly_day": 8}, "weekly_day"),
            ({"work_dir": ""}, "work_dir"),
        ):
            with self.subTest(change=change):
                self.assertIn(word, " ".join(sb.problems_in({**good, **change})))

    def test_series_of_removed_destinations_are_stale(self):
        self.assertTrue(sb.stale(sb.series("last_status", destination="/old"), {"/a"}))
        self.assertFalse(sb.stale(sb.series("last_status", destination="/a"), {"/a"}))
        self.assertFalse(sb.stale(sb.series("restore_test_status"), {"/a"}))

    def test_metrics_lines(self):
        values = {sb.series("last_status", destination="/b"): 1, sb.series("last_run_timestamp_seconds"): 5}
        text = prom.render(values)
        self.assertEqual(
            text,
            'slurm_backup_last_run_timestamp_seconds{} 5\nslurm_backup_last_status{destination="/b"} 1\n',
        )
        self.assertEqual(prom.parse(text), {k: str(v) for k, v in values.items()})


class Backup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        bin_dir = base / "bin"
        bin_dir.mkdir()
        dump = bin_dir / "mariadb-dump"
        dump.write_text("#!/bin/sh\necho 'CREATE TABLE t (i int);'\n")
        dump.chmod(dump.stat().st_mode | stat.S_IEXEC)
        state = base / "slurmctld"
        state.mkdir()
        (state / "job_state").write_text("jobs")
        key = base / "munge.key"
        key.write_bytes(b"k" * 32)
        self.cfg = {
            "database": "slurm_acct_db",
            "state_dir": str(state),
            "munge_key": str(key),
            "weekly_day": 7,
            "textfile_dir": str(base / "prom"),
            "textfile": "slurm-backup.prom",
            "metrics_file": str(base / "state" / "slurm-backup.prom"),
            "work_dir": str(base / "state"),
            "destinations": [
                {"path": str(base / "primary"), "munge_key": True, "daily": 2},
                {"path": str(base / "secondary")},
            ],
        }
        self.base = base
        # The fake dump and the programs the tool runs, never the host's database client
        # (the controller runs the accounting database): tests/lib/sandbox_path.py.
        self.env = mock.patch.dict(os.environ, {"PATH": sandbox_path(bin_dir, TOOLS)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_on(self, day, weekday):
        fake = mock.MagicMock(wraps=sb.dt.datetime)
        fake.now.return_value = sb.dt.datetime.strptime(day, "%Y-%m-%d")
        with mock.patch.object(sb.dt, "datetime", fake):
            return sb.backup(self.cfg)

    def test_a_run_writes_both_destinations_with_checksums(self):
        self.assertEqual(self.run_on("2026-11-21", 6), 0)
        primary = self.base / "primary" / "daily" / "2026-11-21"
        secondary = self.base / "secondary" / "daily" / "2026-11-21"
        self.assertEqual(
            sorted(os.listdir(primary)), sorted([sb.DB_FILE, sb.STATE_FILE, sb.MUNGE_FILE, sb.SUMS_FILE])
        )
        self.assertNotIn(sb.MUNGE_FILE, os.listdir(secondary))
        self.assertNotIn(sb.MUNGE_FILE, (secondary / sb.SUMS_FILE).read_text())
        with gzip.open(primary / sb.DB_FILE) as f:
            self.assertIn(b"CREATE TABLE", f.read())
        for line in (primary / sb.SUMS_FILE).read_text().splitlines():
            digest, name = line.split()
            self.assertEqual(sb.sha256(primary / name), digest)
        self.assertEqual(oct((primary / sb.MUNGE_FILE).stat().st_mode & 0o777), "0o600")
        prom = (self.base / "prom" / "slurm-backup.prom").read_text()
        self.assertIn(f'slurm_backup_last_status{{destination="{self.base / "secondary"}"}} 1', prom)

    def test_weekly_copy_and_retention(self):
        for day in ("2026-11-20", "2026-11-21", "2026-11-22"):  # 22nd is a Sunday
            self.run_on(day, 0)
        self.assertEqual(sorted(os.listdir(self.base / "primary" / "daily")), ["2026-11-21", "2026-11-22"])
        self.assertEqual(os.listdir(self.base / "primary" / "weekly"), ["2026-11-22"])
        self.assertEqual(len(os.listdir(self.base / "secondary" / "daily")), 3)

    def prom(self):
        return (self.base / "prom" / "slurm-backup.prom").read_text()

    def test_failed_dump_fails_every_destination_and_is_reported(self):
        (self.base / "bin" / "mariadb-dump").write_text("#!/bin/sh\necho partial; exit 3\n")
        self.assertEqual(self.run_on("2026-11-21", 6), 1)
        for dest in ("primary", "secondary"):
            self.assertIn(f'slurm_backup_last_status{{destination="{self.base / dest}"}} 0', self.prom())
            self.assertFalse((self.base / dest / "daily").exists())
        self.assertIn("slurm_backup_last_run_timestamp_seconds", self.prom())

    def test_a_state_file_replaced_during_the_archive_is_skipped(self):
        real = sb.os.walk

        def walk(root):
            for dirpath, dirnames, filenames in real(root):
                yield dirpath, dirnames, [*filenames, "job_state.new"]  # renamed away since

        with mock.patch.object(sb.os, "walk", walk):
            self.assertEqual(self.run_on("2026-11-21", 6), 0)

    def test_a_set_appears_only_when_complete(self):
        stale = self.base / "primary" / "daily" / ".2026-11-20.partial"
        stale.mkdir(parents=True)
        real = sb.run_as

        def run_as(owner, argv, timeout, **kwargs):
            if argv[0] == "mv":
                raise sb.proc.CalledProcessError(1, argv)
            return real(owner, argv, timeout, **kwargs)

        with mock.patch.object(sb, "run_as", run_as):
            self.assertEqual(self.run_on("2026-11-21", 6), 1)
        self.assertNotIn("2026-11-21", os.listdir(self.base / "primary" / "daily"))
        self.assertEqual(self.run_on("2026-11-21", 6), 0)
        self.assertEqual(os.listdir(self.base / "primary" / "daily"), ["2026-11-21"])

    def test_a_removed_destination_loses_its_series(self):
        self.run_on("2026-11-21", 6)
        self.cfg["destinations"] = self.cfg["destinations"][:1]
        self.run_on("2026-11-22", 7)
        self.assertNotIn(str(self.base / "secondary"), self.prom())
        self.assertIn(str(self.base / "primary"), self.prom())

    def test_restore_test_without_a_backup_is_reported(self):
        self.assertEqual(sb.restore_test(self.cfg), 1)
        self.assertIn("slurm_backup_restore_test_status{} 0", self.prom())

    def test_restore_test_of_a_set_without_checksums_is_reported(self):
        self.run_on("2026-11-21", 6)
        (self.base / "primary" / "daily" / "2026-11-21" / sb.SUMS_FILE).unlink()
        self.assertEqual(sb.restore_test(self.cfg), 1)
        self.assertIn("slurm_backup_restore_test_status{} 0", self.prom())

    def test_fetch_checks_every_file_and_trusts_no_path(self):
        self.run_on("2026-11-21", 6)
        into = self.base / "fetched"
        into.mkdir()
        newest, problems = sb.fetch_newest(self.cfg["destinations"][0], into, 60)
        self.assertEqual((newest.name, problems), ("2026-11-21", []))
        sums = self.base / "primary" / "daily" / "2026-11-21" / sb.SUMS_FILE
        sums.write_text(sums.read_text() + "0" * 64 + "  ../outside\n")
        _, problems = sb.fetch_newest(self.cfg["destinations"][0], into, 60)
        self.assertIn("unexpected entry", " ".join(problems))
        self.assertFalse((self.base / "outside").exists())

    def test_a_malformed_checksum_line_is_a_problem_not_a_crash(self):
        self.run_on("2026-11-21", 6)
        sums = self.base / "primary" / "daily" / "2026-11-21" / sb.SUMS_FILE
        sums.write_text(sums.read_text() + "garbage\n")
        _, problems = sb.fetch_newest(self.cfg["destinations"][0], self.base, 60)
        self.assertIn("malformed line", " ".join(problems))
        self.assertEqual(sb.restore_test(self.cfg), 1)
        self.assertIn("slurm_backup_restore_test_status{} 0", self.prom())

    def test_a_hung_destination_fails_alone_and_is_reported(self):
        real = sb.run_as

        def run_as(owner, argv, timeout, **kwargs):
            if str(self.base / "secondary") in " ".join(argv):
                raise sb.proc.TimeoutExpired(argv, timeout)
            return real(owner, argv, timeout, **kwargs)

        with mock.patch.object(sb, "run_as", run_as):
            self.assertEqual(self.run_on("2026-11-21", 6), 1)
        prom_text = (self.base / "prom" / "slurm-backup.prom").read_text()
        self.assertIn(f'slurm_backup_last_status{{destination="{self.base / "secondary"}"}} 0', prom_text)
        self.assertIn(f'slurm_backup_last_status{{destination="{self.base / "primary"}"}} 1', prom_text)

    def test_metrics_survive_a_lost_textfile_dir(self):
        self.run_on("2026-11-21", 6)
        published = self.base / "prom" / "slurm-backup.prom"
        published.unlink()  # a reboot empties the tmpfs textfile directory
        self.assertEqual(sb.publish(self.cfg), 0)
        self.assertIn("slurm_backup_last_success_timestamp_seconds", published.read_text())

    def test_publish_dates_every_destination_and_the_restore_test_before_their_first_run(self):
        # Nothing has run: these times are what the staleness alerts count from.
        self.assertEqual(sb.publish(self.cfg), 0)
        text = (self.base / "prom" / "slurm-backup.prom").read_text()
        for dest in self.cfg["destinations"]:
            self.assertIn(
                f'slurm_backup_destination_configured_timestamp_seconds{{destination="{dest["path"]}"}}', text
            )
        self.assertIn("slurm_backup_restore_test_configured_timestamp_seconds{}", text)
        self.assertNotIn("last_status", text)

    def test_the_configured_times_are_kept_and_a_new_destination_gets_its_own(self):
        self.run_on("2026-11-20", 5)
        first = sb.parse((self.base / "state" / "slurm-backup.prom").read_text())
        self.cfg["destinations"].append({"path": str(self.base / "third")})
        self.run_on("2026-11-22", 0)
        later = sb.parse((self.base / "prom" / "slurm-backup.prom").read_text())
        for k, v in first.items():
            if "configured" in k[0]:
                self.assertEqual(later[k], v)
        third = sb.series("destination_configured_timestamp_seconds", destination=str(self.base / "third"))
        self.assertEqual(int(later[third]), int(sb.dt.datetime(2026, 11, 22).timestamp()))


class Main(unittest.TestCase):
    def test_usage(self):
        self.assertEqual(sb.main([]), 2)

    def test_a_bad_configuration_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "c.json"
            cfg.write_text(json.dumps({"destinations": [{"path": "/a", "daily": 0}]}))
            for command in ("check", "backup", "restore-test"):
                with self.subTest(command=command):
                    self.assertEqual(sb.main([command, str(cfg)]), 2)


if __name__ == "__main__":
    unittest.main()
