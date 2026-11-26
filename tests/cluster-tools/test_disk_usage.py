"""Tests for usage disk (cluster_tools.disk_usage): one person's figures from Prometheus."""

import re
import sys
import unittest

from common import HERE, run_tool
from cluster_tools import disk_usage, render
from cluster_tools.prometheus import Error as PrometheusError

MATCHER = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


class FakePrometheus:
    """Answers an instant query from SERIES {metric: [labels with "value"]}, keeping the
    series whose labels match every matcher of the query's selector; records the queries."""

    def __init__(self, series, error=None):
        self.series, self.error, self.asked = series, error, []

    def query(self, expr, at=None):
        self.asked.append(expr)
        if self.error:
            raise PrometheusError(self.error)
        found = re.search(r"(\w+)\{([^}]*)\}", expr) or re.search(r"\((\w+)\)()", expr)
        metric, selector = found.groups()
        want = dict(MATCHER.findall(selector))
        return [
            {"metric": {k: v for k, v in s.items() if k != "value"}, "value": [0, str(s["value"])]}
            for s in self.series.get(metric, [])
            if all(s.get(k) == v for k, v in want.items())
        ]


def quota(user, host, fs, used, files, warn=0, hard=0, file_warn=0, file_hard=0):
    """The disk quota metrics of one person on one filesystem."""
    base = {"host": host, "filesystem": fs, "user": user}
    out = {
        "disk_quota_used_bytes": [{**base, "value": used}],
        "disk_quota_used_inodes": [{**base, "value": files}],
    }
    for metric, kind, v in (
        ("disk_quota_limit_bytes", "warn", warn),
        ("disk_quota_limit_bytes", "hard", hard),
        ("disk_quota_limit_inodes", "warn", file_warn),
        ("disk_quota_limit_inodes", "hard", file_hard),
    ):
        if v:
            out.setdefault(metric, []).append({**base, "kind": kind, "value": v})
    return out


def merged(*parts):
    out = {}
    for p in parts:
        for k, v in p.items():
            out.setdefault(k, []).extend(v)
    return out


T = 2**40
SERIES = merged(
    quota("kim", "nfs1", "/home", 4.5 * T, 900_000, warn=4 * T, hard=5 * T, file_hard=1_000_000),
    quota("kim", "scratch1", "/scratch", 2 * T, 10, hard=2 * T),
    quota("lee", "nfs1", "/home", 9 * T, 5),
    {
        "disk_cleanup_candidate_bytes": [
            {"host": "nfs1", "filesystem": "/home", "path": "/home/kim/old", "user": "kim", "value": 2**30},
            {"host": "nfs1", "filesystem": "/home", "path": "/home/kim/big", "user": "kim", "value": 2**40},
            {"host": "nfs1", "filesystem": "/home", "path": "/home/lee/x", "user": "lee", "value": 2**41},
        ],
        "disk_cleanup_candidate_unused_days": [
            {"host": "nfs1", "filesystem": "/home", "path": "/home/kim/old", "user": "kim", "value": 400},
            {"host": "nfs1", "filesystem": "/home", "path": "/home/kim/big", "user": "kim", "value": 90},
            {"host": "nfs1", "filesystem": "/home", "path": "/home/lee/x", "user": "lee", "value": 90},
        ],
        "disk_cleanup_scan_timestamp_seconds": [
            {"host": "nfs1", "filesystem": "/home", "value": 1_795_000_000},
            {"host": "scratch1", "filesystem": "/scratch", "value": 1_795_500_000},
        ],
        "io_usage_job_read_bytes_total": [
            {"host": "gpu1", "job_id": "11", "user": "kim", "value": 3 * 2**30},
            {"host": "gpu2", "job_id": "12", "user": "kim", "value": 2**20},
            {"host": "gpu2", "job_id": "13", "user": "lee", "value": 2**40},
        ],
        "io_usage_job_written_bytes_total": [
            {"host": "gpu2", "job_id": "12", "user": "kim", "value": 5 * 2**30},
        ],
        "io_usage_login_read_bytes_total": [
            {"host": "login1", "device": "sda", "user": "kim", "value": 0},
        ],
        "io_usage_login_written_bytes_total": [
            {"host": "login1", "device": "sda", "user": "kim", "value": 0},
        ],
    },
)


class QueryTests(unittest.TestCase):
    def test_every_query_selects_the_person(self):
        api = FakePrometheus(SERIES)
        disk_usage.collect(api, 'a"b', 10)
        self.assertTrue(api.asked)
        for expr in api.asked:
            if expr != disk_usage.SCANNED:  # when a filesystem was scanned: everyone's
                self.assertIn('user="a\\"b"', expr)

    def test_the_dashboard_reads_the_same_definitions(self):
        sys.path.insert(0, str(HERE.parents[1] / "scripts/dashboards"))
        import storage_person

        exprs = [t["expr"] for p in storage_person.build()["panels"] for t in p.get("targets", [])]
        sel = storage_person.PERSON
        for figure, expr in disk_usage.space(sel).items():
            self.assertTrue(any(expr in e for e in exprs), figure)
        for figure, expr in disk_usage.folders(sel).items():
            self.assertTrue(any(expr in e for e in exprs), figure)


class CollectTests(unittest.TestCase):
    def test_one_persons_space_files_and_levels(self):
        data = disk_usage.collect(FakePrometheus(SERIES), "kim", 10)
        self.assertEqual(
            data["space"],
            [
                {"where": "nfs1:/home", "used": 4.5 * T, "warn": 4 * T, "hard": 5 * T,
                 "files": 900_000, "file_warn": None, "file_hard": 1_000_000},
                {"where": "scratch1:/scratch", "used": 2 * T, "warn": None, "hard": 2 * T,
                 "files": 10, "file_warn": None, "file_hard": None},
            ],
        )  # fmt: skip

    def test_folders_longest_unused_and_largest_first(self):
        data = disk_usage.collect(FakePrometheus(SERIES), "kim", 10)
        self.assertEqual([f["path"] for f in data["folders"]], ["/home/kim/big", "/home/kim/old"])
        self.assertEqual(data["folders"][1]["unused_days"], 400)

    def test_top_caps_each_list(self):
        data = disk_usage.collect(FakePrometheus(SERIES), "kim", 1)
        self.assertEqual(len(data["folders"]), 1)


class RenderTests(unittest.TestCase):
    def theme(self):
        return render.Theme(color=False, cols=100)

    def test_rows_with_levels(self):
        text = disk_usage.render(disk_usage.collect(FakePrometheus(SERIES), "kim", 10), self.theme())
        self.assertRegex(text, r"nfs1:/home\s+4\.5T\s+4T\s+5T\s+900,000\s+-\s+1,000,000")
        self.assertRegex(text, r"/home/kim/big\s+1T\s+90d\s+nfs1:/home")

    def test_the_folders_say_when_their_filesystem_was_scanned(self):
        # 2026-11-27: the help said "the last daily scan"; the home filesystem's is weekly
        data = disk_usage.collect(FakePrometheus(SERIES), "kim", 10)
        self.assertEqual(data["scans"][0], {"where": "nfs1:/home", "time": 1_795_000_000})
        text = disk_usage.render(data, self.theme())
        self.assertIn(f"scanned nfs1:/home {disk_usage._when(1_795_000_000)}", text)
        self.assertNotIn("scratch1:/scratch 20", text, "only the filesystems whose folders are shown")
        self.assertNotIn("daily scan", disk_usage.COMMAND.help(True))

    def test_past_a_level_is_painted(self):
        self.assertEqual(disk_usage._level(5, 4, 10), "warn")
        self.assertEqual(disk_usage._level(10, 4, 10), "crit")
        self.assertEqual(disk_usage._level(3, 4, 10), "text")
        self.assertEqual(disk_usage._level(3, 0, 0), "text")

    def test_nothing_counted_is_said(self):
        text = disk_usage.render(disk_usage.collect(FakePrometheus({}), "nobody", 10), self.theme())
        self.assertIn("no space of nobody is counted on any filesystem", text)


class CommandTests(unittest.TestCase):
    def test_help_names_the_command(self):
        done = run_tool("disk_usage", "-h")
        self.assertEqual(done.returncode, 0)
        self.assertIn("Usage: usage disk [OPTIONS]", done.stdout)


if __name__ == "__main__":
    unittest.main()
