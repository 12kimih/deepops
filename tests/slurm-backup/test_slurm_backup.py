"""slurm_backup.py: retention, metrics, and a full backup run against stub tools."""

import gzip
import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "slurm_backup", ROOT / "roles" / "slurm_backup" / "files" / "slurm_backup.py"
)
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)


class Pure(unittest.TestCase):
    def test_to_remove_keeps_the_newest(self):
        names = ["2026-11-01", "2026-11-03", "2026-11-02", "notes", "2026-10-31"]
        self.assertEqual(sb.to_remove(names, 2), ["2026-10-31", "2026-11-01"])
        self.assertEqual(sb.to_remove(names, 10), [])
        self.assertEqual(sb.to_remove(names, 0), ["2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03"])

    def test_metrics_lines(self):
        values = {sb.series("last_status", destination="/b"): 1, sb.series("last_run_timestamp_seconds"): 5}
        text = sb.metrics("slurm_backup", values)
        self.assertEqual(
            text,
            'slurm_backup_last_run_timestamp_seconds{} 5\nslurm_backup_last_status{destination="/b"} 1\n',
        )
        self.assertEqual(sb.parse_metrics("slurm_backup", text), {k: str(v) for k, v in values.items()})


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
            "destinations": [
                {"path": str(base / "primary"), "munge_key": True, "daily": 2},
                {"path": str(base / "secondary")},
            ],
        }
        self.base = base
        self.env = mock.patch.dict(os.environ, {"PATH": f"{bin_dir}:{os.environ['PATH']}"})
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

    def test_failed_dump_fails_the_run(self):
        (self.base / "bin" / "mariadb-dump").write_text("#!/bin/sh\nexit 3\n")
        with self.assertRaises(RuntimeError):
            self.run_on("2026-11-21", 6)

    def test_metrics_survive_a_lost_textfile_dir(self):
        self.run_on("2026-11-21", 6)
        published = self.base / "prom" / "slurm-backup.prom"
        published.unlink()  # a reboot empties the tmpfs textfile directory
        self.assertEqual(sb.publish(self.cfg), 0)
        self.assertIn("slurm_backup_last_success_timestamp_seconds", published.read_text())


class Main(unittest.TestCase):
    def test_usage(self):
        self.assertEqual(sb.main([]), 2)


if __name__ == "__main__":
    unittest.main()
