"""cluster_tools.submit_holds: who may not submit for now. Someone at a disk hard limit,
with the reason they are shown, from disk_from on; the file job_submit.lua reads, and a
failed Prometheus read that leaves the last file in place."""

import json
import sys
import tempfile
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


class HoldTests(unittest.TestCase):
    def data(self):
        return disk_report.collect(site(), NOW)

    def test_only_a_hard_limit_holds_with_the_reason(self):
        held = submit_holds.holds(self.data(), {"disk_from": 0}, NOW, UIDS.get)
        self.assertEqual(list(held), [1002], "bob is at his hard limit; alice only past her warn level")
        self.assertIn("at the hard limit 20G", held[1002])
        self.assertIn("New jobs are held until you free space there", held[1002])

    def test_no_one_before_the_grace_ends_nor_without_an_account(self):
        self.assertEqual(submit_holds.holds(self.data(), {"disk_from": NOW + 1}, NOW, UIDS.get), {})
        with mock.patch("sys.stderr"):
            self.assertEqual(submit_holds.holds(self.data(), {"disk_from": 0}, NOW, {}.get), {})
        self.assertEqual(submit_holds.holds(None, {"disk_from": 0}, NOW, UIDS.get), {})

    def test_the_file_job_submit_lua_reads(self):
        # A reason is one line whatever it holds: the file is a line per person.
        self.assertEqual(
            submit_holds.text({1002: "a\nreason  here"}, NOW + 0.5),
            f"generated {NOW}\n1002 a reason here\n",
        )
        self.assertEqual(submit_holds.text({}, NOW), f"generated {NOW}\n")

    def test_a_failed_read_keeps_the_last_file(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, holds, prom = Path(d, "c.json"), Path(d, "holds"), Path(d, "h.prom")
            cfg.write_text(
                json.dumps(
                    {"prometheus_url": "http://x", "file": str(holds), "disk_from": 0, "textfile": str(prom)}
                )
            )
            holds.write_text("generated 1\n")
            away = mock.patch.object(submit_holds.disk_report, "collect", side_effect=PrometheusError("away"))
            with away, mock.patch("sys.stderr"):
                self.assertEqual(submit_holds.main(["submit-holds", str(cfg)]), 1)
            self.assertEqual(holds.read_text(), "generated 1\n")
            with mock.patch.object(submit_holds.disk_report, "collect", return_value=None):
                self.assertEqual(submit_holds.main(["submit-holds", str(cfg)]), 0)
            self.assertTrue(holds.read_text().startswith("generated "))
            self.assertIn("submit_holds_people{} 0", prom.read_text())


if __name__ == "__main__":
    unittest.main()
