"""Tests for usage io (cluster_tools.io_load): one person's I/O and their nodes' load on
the shared storage, from Prometheus."""

import sys
import unittest

from common import HERE
from test_disk_usage import SERIES, FakePrometheus

from cluster_tools import io_load, render


class IoTests(unittest.TestCase):
    def test_the_io_window_is_the_hours_asked(self):
        api = FakePrometheus(SERIES)
        io_load.collect(api, "kim", 6, 10)
        io = [e for e in api.asked if "io_usage" in e]
        self.assertEqual(len(io), 4)
        self.assertTrue(all("increase(" in e and "[6h]" in e for e in io), io)

    def test_io_by_job_most_first_idle_disks_left_out(self):
        data = io_load.collect(FakePrometheus(SERIES), "kim", 24, 10)
        self.assertEqual(
            data["jobs"],
            [{"host": "gpu2", "job_id": "12", "read": 2**20, "written": 5 * 2**30},
             {"host": "gpu1", "job_id": "11", "read": 3 * 2**30, "written": 0.0}],
        )  # fmt: skip
        self.assertEqual(data["logins"], [])
        self.assertEqual(len(io_load.collect(FakePrometheus(SERIES), "kim", 24, 1)["jobs"]), 1)

    def test_rows(self):
        text = io_load.render(
            io_load.collect(FakePrometheus(SERIES), "kim", 24, 10), render.Theme(color=False, cols=100), 0.4
        )
        self.assertRegex(text, r"12\s+gpu2\s+1M\s+5G")
        self.assertRegex(text, r"I/O of logins\s+last 24h\s+none")

    def test_the_dashboard_reads_the_same_definitions(self):
        sys.path.insert(0, str(HERE.parents[1] / "scripts/dashboards"))
        import storage_person

        exprs = [t["expr"] for p in storage_person.build()["panels"] for t in p.get("targets", [])]
        for who, direction in io_load.IO:
            expr = io_load.io(who, direction, storage_person.PERSON, "5m", "rate")
            self.assertTrue(any(expr in e for e in exprs), (who, direction))


if __name__ == "__main__":
    unittest.main()
