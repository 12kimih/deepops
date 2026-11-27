"""cluster_tools.submit_holds: who may not submit for now. Someone at a disk hard limit,
with the reason they are shown, from disk_from on; the file job_submit.lua reads, and a
failed Prometheus read that leaves the last file in place."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
sys.path.insert(0, str(ROOT / "tests/slurm-usage-report"))
from cluster_tools import disk_report, submit_holds  # noqa: E402
from cluster_tools.prometheus import Error as PrometheusError  # noqa: E402
from test_disk import NOW, site  # noqa: E402

UIDS = {"alice": 1001, "bob": 1002, "carol": 1003}
DISK = {"disk": True, "disk_from": 0, "ends": {}}
DAY = 86400


class HoldTests(unittest.TestCase):
    def data(self):
        return disk_report.collect(site(), NOW)

    def test_only_a_hard_limit_holds_with_the_reason(self):
        held = submit_holds.holds(self.data(), DISK, NOW, UIDS.get)
        self.assertEqual(list(held), [1002], "bob is at his hard limit; alice only past her warn level")
        self.assertIn("at the hard limit 20G", held[1002])
        self.assertIn("New jobs are held until you free space there", held[1002])
        # The command that shows one's own use anywhere, not quota on a host they cannot log in to.
        self.assertIn("(`usage disk` shows it)", held[1002])

    def test_no_one_before_the_grace_ends_nor_without_an_account(self):
        self.assertEqual(submit_holds.holds(self.data(), {**DISK, "disk_from": NOW + 1}, NOW, UIDS.get), {})
        with mock.patch("sys.stderr"):
            self.assertEqual(submit_holds.holds(self.data(), DISK, NOW, {}.get), {})
        self.assertEqual(submit_holds.holds(None, DISK, NOW, UIDS.get), {})

    def test_the_file_job_submit_lua_reads(self):
        # A reason is one line whatever it holds: the file is a line per person.
        self.assertEqual(
            submit_holds.text({1002: "a\nreason  here"}, NOW + 0.5),
            f"generated {NOW}\n1002 a reason here\n",
        )
        self.assertEqual(submit_holds.text({}, NOW), f"generated {NOW}\n")

    def test_the_metrics_carry_how_long_job_submit_lua_trusts_the_file(self):
        # SubmitHoldsStale compares the last run with half of it, so the alert and
        # job_submit.lua read one value (submit_holds_max_age).
        out = submit_holds.metrics({}, {**DISK, "max_age": 1800}, NOW)
        self.assertIn("submit_holds_max_age_seconds{} 1800\n", out)
        rules = (ROOT / "roles/prometheus/files/rules/submit-holds.yml").read_text()
        self.assertIn("> submit_holds_max_age_seconds / 2", rules)
        self.assertNotIn("max_age_seconds", submit_holds.metrics({}, DISK, NOW))

    def test_a_config_written_before_max_age_still_loads(self):
        with tempfile.TemporaryDirectory() as d:
            old = {**DISK, "prometheus_url": "http://x", "file": "/f", "textfile": "/t"}
            cfg = Path(d, "c.json")
            cfg.write_text(json.dumps(old))
            self.assertNotIn("max_age", submit_holds.load_config(str(cfg)))
            cfg.write_text(json.dumps({**old, "max_age": 1800}))
            self.assertEqual(submit_holds.load_config(str(cfg))["max_age"], 1800)

    def test_a_failed_read_keeps_the_last_file(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, holds, prom = Path(d, "c.json"), Path(d, "holds"), Path(d, "h.prom")
            cfg.write_text(
                json.dumps({**DISK, "prometheus_url": "http://x", "file": str(holds), "textfile": str(prom)})
            )
            holds.write_text("generated 1\n1002 at the hard limit\n")
            away = mock.patch.object(submit_holds.disk_report, "collect", side_effect=PrometheusError("away"))
            with away, mock.patch("sys.stderr"):
                self.assertEqual(submit_holds.main(["submit-holds", str(cfg)]), 3)
            self.assertTrue(holds.read_text().endswith("\n1002 at the hard limit\n"))
            self.assertFalse(prom.exists(), "no success is recorded: SubmitHoldsStale fires")
            with mock.patch.object(submit_holds.disk_report, "collect", return_value=None):
                self.assertEqual(submit_holds.main(["submit-holds", str(cfg)]), 0)
            self.assertTrue(holds.read_text().startswith("generated "))
            self.assertIn("submit_holds_people{} 0", prom.read_text())


class EndsTests(unittest.TestCase):
    ENDS = {"carol": {"ends": "2026-11-21", "login_expires": "2026-12-21"}}

    def at(self, day):
        return time.mktime(time.strptime(day + " 00:00:01", "%Y-%m-%d %H:%M:%S"))

    def test_held_from_the_day_after_the_end_with_the_login_date(self):
        cfg = {**DISK, "disk": False, "ends": self.ENDS}
        self.assertEqual(submit_holds.holds(None, cfg, self.at("2026-11-21"), UIDS.get), {})
        held = submit_holds.holds(None, cfg, self.at("2026-11-22"), UIDS.get)
        self.assertEqual(list(held), [1003])
        self.assertIn("Your membership ended on 2026-11-21", held[1003])
        self.assertIn("until 2026-12-21", held[1003])

    def test_without_disk_the_disk_metrics_hold_no_one(self):
        data = disk_report.collect(site(), NOW)
        self.assertEqual(submit_holds.holds(data, {**DISK, "disk": False}, NOW, UIDS.get), {})

    def test_prometheus_down_and_an_end_passing_still_writes_the_hold(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, holds, prom = Path(d, "c.json"), Path(d, "holds"), Path(d, "h.prom")
            cfg.write_text(json.dumps({**DISK, "ends": self.ENDS, "prometheus_url": "http://x",
                                       "file": str(holds), "textfile": str(prom)}))  # fmt: skip
            holds.write_text("generated 1\n1002 at the hard limit\n1003 Your membership ended on old\n")
            away = mock.patch.object(submit_holds.disk_report, "collect", side_effect=PrometheusError("away"))
            today = mock.patch.object(submit_holds.time, "time", return_value=self.at("2026-11-22"))
            with away, today, mock.patch("sys.stderr"), mock.patch.object(submit_holds, "uid_of", UIDS.get):
                self.assertEqual(submit_holds.main(["submit-holds", str(cfg)]), 3)
            lines = holds.read_text().splitlines()
            self.assertEqual(lines[1], "1002 at the hard limit")
            self.assertTrue(lines[2].startswith("1003 Your membership ended on 2026-11-21."))
            self.assertEqual(len(lines), 3)


if __name__ == "__main__":
    unittest.main()
