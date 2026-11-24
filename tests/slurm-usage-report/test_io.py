"""The I/O notices, cluster_tools.io_report, and their run in cluster_tools.usage_report
--io (no cluster needed)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROLES = Path(__file__).resolve().parents[2] / "roles"
sys.path.insert(0, str(ROLES / "cluster_tools_lib/files"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests/lib"))
from fake_slack import FakeDM  # noqa: E402
from cluster_tools import io_report  # noqa: E402
from cluster_tools import usage_report as report  # noqa: E402

NOW = 1_800_000_000
M = 2**20
MOUNTS = {"/home": {"read_bps": 1000 * M, "write_bps": 800 * M}}


class Prom:
    """n1 writes 400 MiB/s to /home (half the disk's writes); its jobs ranked by writes."""

    def __init__(self, hot=True):
        self.hot, self.asked = hot, []

    def query(self, expr, at=None):
        self.asked.append(expr)
        if expr.startswith("min_over_time(host_mount:nfs_write_bytes:rate5m"):
            limit = float(expr.rsplit(">", 1)[1])
            return (
                [{"metric": {"host": "n1", "mountpoint": "/home"}, "value": [NOW, str(400 * M)]}]
                if self.hot and 400 * M > limit
                else []
            )
        if expr.startswith("min_over_time("):
            return []
        if expr.startswith("topk(") and "io_usage_job_written_bytes_total" in expr and 'host="n1"' in expr:
            return [{"metric": {"job_id": "11", "user": "bob"}, "value": [NOW, str(50 * M)]},
                    {"metric": {"job_id": "10", "user": "alice"}, "value": [NOW, str(300 * M)]},
                    {"metric": {"job_id": "12", "user": "root"}, "value": [NOW, str(1 * M)]}]  # fmt: skip
        return []


class Collect(unittest.TestCase):
    def test_a_node_above_the_share_for_the_window_with_its_jobs_ranked(self):
        prom = Prom()
        hot = io_report.collect(prom, MOUNTS, 0.4, 15, 3)
        self.assertEqual(len(hot), 1)
        item = hot[0]
        self.assertEqual((item["host"], item["mount"], item["direction"]), ("n1", "/home", "write"))
        self.assertEqual([j[0] for j in item["jobs"]], ["10", "11", "12"])
        self.assertIn(
            'min_over_time(host_mount:nfs_write_bytes:rate5m{mountpoint="/home"}[15m]) > ', prom.asked[1]
        )
        self.assertIn('topk(3, sum by (job_id, user) (rate(io_usage_job_written_bytes_total{host="n1"}[15m])))',
                      prom.asked[2])  # fmt: skip

    def test_below_the_share_is_nothing(self):
        self.assertEqual(io_report.collect(Prom(), MOUNTS, 0.6, 15, 3), [])

    def test_texts_say_what_the_ranking_is(self):
        item = io_report.collect(Prom(), MOUNTS, 0.4, 15, 3)[0]
        dm = io_report.dm_text(item, 0, "Use /scratch.")
        self.assertIn(
            "node `n1` has been writing to `/home` at 400M/s, 50% of what the disk behind it serves", dm
        )
        self.assertIn("Your job `10` did the most writing on that node (300M/s", dm)
        self.assertIn("not its own share of the disk", dm)
        self.assertTrue(dm.endswith("Use /scratch."))
        self.assertIn("was number 2 of the jobs by writing", io_report.dm_text(item, 1, ""))
        self.assertEqual(
            io_report.admin_text([item]),
            "I/O: `n1` writing to `/home` at 400M/s (50% of the disk) for 15 min; "
            "top jobs: 10 (alice, 300M/s), 11 (bob, 50M/s), 12 (root, 1M/s)",
        )


class Run(unittest.TestCase):
    CFG = dict(prometheus_url="", dm_log_channel="", slack_token="t", email_domain="", user_map={"alice": "UALICE", "bob": "UBOB"},
               admin_channel="#admins", admin_mention="", io_mounts=MOUNTS, io_share=0.4, io_minutes=15,
               io_top_jobs=3, io_repeat_hours=6, io_advice="Use /scratch.")  # fmt: skip

    def run_io(self, prom, state, now=NOW, to=None, fail=()):
        posted = []

        class Slack(FakeDM):
            token = "t"

            def __init__(self, *_, **__):
                pass

            def post(self, channel, text, blocks=None, thread_ts=None):
                if channel in fail:
                    raise RuntimeError("Slack chat.postMessage failed: channel_not_found")
                posted.append((channel, text))
                return "ts"

        cfg = dict(self.CFG, state_file=f"{state}/state.json")
        args = mock.Mock(dry_run=False, to=to)
        with (
            mock.patch.object(report, "Prometheus", lambda url: prom),
            mock.patch.object(report, "Slack", Slack),
            mock.patch.object(report.time, "time", lambda: now),
        ):
            try:
                report.io(cfg, args)
                error = None
            except RuntimeError as e:
                error = str(e)
        saved = Path(f"{state}/io.json")
        return posted, error, json.loads(saved.read_text()) if saved.exists() else None

    def test_the_top_jobs_users_and_the_admins_are_told_once_then_when_due(self):
        with tempfile.TemporaryDirectory() as d:
            posted, error, saved = self.run_io(Prom(), d)
            self.assertIsNone(error)
            self.assertEqual(
                sorted(p[0] for p in posted), ["#admins", "UALICE", "UBOB"], "root's job: admins only"
            )
            self.assertIn("did the most writing", dict(posted)["UALICE"])
            self.assertEqual(len(saved["told"]), 3)
            again, _, _ = self.run_io(Prom(), d, now=NOW + 3600)
            self.assertEqual(again, [], "within io_repeat_hours")
            later, _, _ = self.run_io(Prom(), d, now=NOW + 7 * 3600)
            self.assertEqual(len(later), 3)

    def test_once_it_cools_the_next_hot_spell_is_told_at_once(self):
        with tempfile.TemporaryDirectory() as d:
            self.run_io(Prom(), d)
            _, _, saved = self.run_io(Prom(hot=False), d, now=NOW + 600)
            self.assertEqual(saved["told"], {})
            posted, _, _ = self.run_io(Prom(), d, now=NOW + 1200)
            self.assertEqual(len(posted), 3)

    def test_one_failed_dm_does_not_stop_the_others(self):
        with tempfile.TemporaryDirectory() as d:
            posted, error, saved = self.run_io(Prom(), d, fail={"UBOB"})
            self.assertIn("notifying bob", error)
            self.assertEqual(sorted(p[0] for p in posted), ["#admins", "UALICE"])
            self.assertNotIn(io_report.user_key({"host": "n1", "mount": "/home", "direction": "write"}, "bob"),
                             saved["told"])  # fmt: skip

    def test_a_preview_posts_the_summary_there_and_records_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            posted, _, saved = self.run_io(Prom(), d, to="UME")
            self.assertEqual([p[0] for p in posted], ["UME"])
            self.assertIsNone(saved)

    def test_no_measured_mount_does_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            prom = Prom()
            with mock.patch.dict(self.CFG, io_mounts={}):
                posted, _, _ = self.run_io(prom, d)
            self.assertEqual((posted, prom.asked), ([], []))


if __name__ == "__main__":
    unittest.main()
