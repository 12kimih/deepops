"""Everything the reports say to people (the status channel's posts, the DMs) is Korean,
and shows no Markdown marker (cluster_tools.rich.problems: "*3개*의" in mrkdwn shows its stars):
no English word outside the names it shows (GPU, Slurm, Grafana, a node's state), a
command or path in backticks, a link, a mention or an emoji. The administrators' channel
and what sbatch prints stay English, as every terminal message does."""

import importlib.util
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
from cluster_tools import disk_report, io_report, rich  # noqa: E402
from cluster_tools import usage_report as report  # noqa: E402


# The disk tests' Prometheus stand-in, the same site.
_spec = importlib.util.spec_from_file_location("disk_fixture", Path(__file__).with_name("test_disk.py"))
disk_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(disk_fixture)

# Names, not words: what the cluster and its tools are called.
NAMES = {"GPU", "QOS", "Slurm", "Grafana", "drain", "down", "node_exporter", "normal"}
FIXTURE = {
    "alice",
    "bob",
    "carol",
    "root",
    "mysql",
    "login",
    "nfs",
    "home",
    "shared",
    "datasets",
    "data",
    "old",
}
DROP = re.compile(r"```.*?```|`[^`]*`|<[^>]*>|\(https?://\S+\)|:[a-z_]+:|/\S*", re.S)


def english(text):
    """The English words in TEXT, names and fixtures left out. A table's cells are the
    cluster's (the reason Slurm gave, a QoS): only its header line is ours."""
    headers = " ".join(re.findall(r"```\n([^\n]*)\n.*?```", text, re.S))
    words = re.findall(r"[A-Za-z]{2,}", DROP.sub(" ", text) + " " + headers)
    return sorted({w for w in words if w not in NAMES and w not in FIXTURE})


def strings(node):
    """What a message shows: its blocks as cluster_tools.rich reads them, or its text."""
    if isinstance(node, dict):
        return rich.read([node])
    if isinstance(node, (list, tuple)):
        return " ".join(strings(v) for v in node)
    return node if isinstance(node, str) else ""


def who(user, mention):
    return f"<@U{user.upper()}>" if mention else user


def a_job(jid, user, idle):
    return {"id": jid, "label": jid, "user": user, "gpus": [("n1", 0), ("n1", 1)], "gpu_type": "a10",
            "runtime": 9000, "qos": "normal", "util": 0.0 if idle else 50.0, "mem_gib": None, "mem_known": 0,
            "idle": idle, "idle_gpus": 2 if idle else 1, "running": True}  # fmt: skip


class Language(unittest.TestCase):
    def assertKorean(self, what, text):
        self.assertEqual(english(text), [], f"{what}: {text[:300]}")

    def assertShown(self, what, message):
        """MESSAGE (blocks, or a text sent as one) shows no Markdown marker (cluster_tools.rich.problems)."""
        if isinstance(message, str):
            message = rich.message(message)
            message = (message["text"], message["blocks"])
        self.assertEqual(rich.problems(message, what), [])

    def test_the_check_finds_english(self):
        self.assertEqual(
            english("Details in the thread  |  <https://x/|Grafana> `scancel 1`"),
            ["Details", "in", "the", "thread"],
        )
        self.assertEqual(
            english("```\nUSER  GPU\nalice  maintenance\n```"), ["USER"], "a header is checked, cells not"
        )

    def test_the_hourly_report_and_its_thread(self):
        jobs = [a_job("1", "alice", True), a_job("2", "bob", False)]
        data = {"jobs": jobs, "idle": jobs[:1], "total_gpus": 16, "now": 0, "due": ["alice"],
                "reasons": [("n2", "drain", "2026-01-01T00:00:00", "root", "maintenance")],
                "pending": {"carol": {"jobs": 1, "gpus": 2, "reasons": Counter({"Priority": 1}), "wait": 60}},
                "dashboard_url": "https://grafana.example/"}  # fmt: skip
        cfg = {"idle_util_threshold": 5, "idle_hours": 2, "notify": ["mention"], "cancel_after_hours": 6}
        (text, summary), thread = report.render(data, cfg, who)
        self.assertShown("card", (text, summary))
        for _, blocks in thread:
            self.assertShown("thread", blocks)
        self.assertShown("cancel", report.cancelled_note(jobs[:1], cfg, who))
        self.assertKorean("text", text)
        self.assertKorean("card", strings(summary))
        for _, blocks in thread:
            self.assertKorean("thread", strings(blocks))
        self.assertKorean("cancel", strings(report.cancelled_note(jobs[:1], cfg, who)))

    def test_the_weekly_summary(self):
        rows = [{"user": "alice", "held": 120, "idle": 90}, {"user": "bob", "held": 10, "idle": 0}]
        self.assertKorean("weekly", strings(report.render_week(rows, who, 5)))
        self.assertShown("weekly", report.render_week(rows, who, 5))
        self.assertKorean("weekly, none", strings(report.render_week([], who, 5)))

    def test_the_disk_report_and_its_thread(self):
        data = disk_report.collect(disk_fixture.site(), disk_fixture.NOW)
        fill = disk_report.fill_rows(data, 0.7)
        for hold in (None, 0, disk_fixture.NOW + disk_report.DAY):
            (text, card), thread = disk_report.render(data, who, 2, people={"alice", "bob"}, hold=hold, fill=fill,
                                                      stale_days=60)  # fmt: skip
            self.assertShown("disk card", (text, card))
            for _, blocks in thread:
                self.assertShown("disk thread", blocks)
            self.assertKorean("text", text)
            self.assertKorean("card", strings(card))
            for _, blocks in thread:
                self.assertKorean("thread", strings(blocks))

    def test_the_dms(self):
        data = disk_report.collect(disk_fixture.site(), disk_fixture.NOW)
        for row in disk_report.over_warn(data):
            self.assertKorean("disk DM", disk_report.dm_text(row, disk_report.hold_note(0, disk_fixture.NOW)))
            self.assertKorean("disk DM", disk_report.dm_text(row))
            self.assertShown("disk DM", disk_report.dm_text(row, disk_report.hold_note(0, disk_fixture.NOW)))
        entry = {"files": 3, "bytes": 2**31, "next": disk_fixture.NOW, "stale_days": 60, "deferred": 2}
        self.assertKorean("purge DM", disk_report.purge_dm_text(("n1", "alice"), entry))
        self.assertShown("purge DM", disk_report.purge_dm_text(("n1", "alice"), entry))
        item = {"host": "n1", "mount": "/home", "direction": "write", "rate": 4e8, "capacity": 8e8, "minutes": 15,
                "jobs": [("10", "alice", 3e8), ("11", "bob", 5e7)]}  # fmt: skip
        for rank in (0, 1):
            self.assertKorean("I/O DM", io_report.dm_text(item, rank, ""))
            self.assertShown("I/O DM", io_report.dm_text(item, rank, ""))
        defaults = yaml.safe_load((ROOT / "roles/slurm_usage_report/defaults/main.yml").read_text())
        jobs = report.job_lines([a_job("1", "alice", True)])
        for key in ("slurm_usage_report_dm_text", "slurm_usage_report_cancel_warn_text"):
            self.assertKorean(key, defaults[key].format(jobs=jobs, count=1, hours="2"))
            self.assertShown(key, defaults[key].format(jobs=jobs, count=1, hours="2"))
        self.assertKorean("io_advice", defaults["slurm_usage_report_io_advice"])


if __name__ == "__main__":
    unittest.main()
