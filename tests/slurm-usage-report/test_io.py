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
        if expr.startswith("topk(") and "job:nfs_write_bytes:rate5m" in expr and 'host="n1"' in expr:
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
        self.assertIn('topk(3, sum by (job_id, user) (avg_over_time(job:nfs_write_bytes:rate5m{host="n1"}[15m])))',
                      prom.asked[2])  # fmt: skip

    def test_a_hot_node_with_no_job_counted_says_so_in_the_hours_line(self):
        """Slurm 26.05's job cgroups carried no job_<id>, so io-usage counted no job for 8
        days (2026-11-27): nobody can be told, and the record says why (the alert
        IoUsageJobsMissing names it to the administrators)."""
        data = {"jobs": [], "idle": [], "total_gpus": 8, "now": NOW, "pending": {}}
        line = report.hour_line(data, hot=[("n1", [])])
        self.assertIn("I/O 과다 `n1` (잡 집계 없음)", line)
        self.assertIn("I/O 과다 `n1` (alice, bob)", report.hour_line(data, hot=[("n1", ["alice", "bob"])]))

    def test_below_the_share_is_nothing(self):
        self.assertEqual(io_report.collect(Prom(), MOUNTS, 0.6, 15, 3), [])

    def test_texts_say_what_the_ranking_is(self):
        item = io_report.collect(Prom(), MOUNTS, 0.4, 15, 3)[0]
        dm = io_report.dm_text(item, 0, "Use /scratch.")
        self.assertIn(
            "`n1` 노드가 `/home`에 쓰고 있습니다. 400M/s로, `/home` 디스크가 클러스터 전체에 낼 수 있는 속도의 50%를",
            dm,
        )
        self.assertIn("그 노드에서 가장 많이 쓴 잡이 `10`입니다 (300M/s", dm)
        self.assertIn(
            "그 노드가 공유 스토리지와 주고받은 양을 노드 안 잡들의 읽기, 쓰기 호출 양에 비례해 나눈 추정치",
            dm,
        )
        self.assertTrue(
            dm.endswith("Use /scratch.\n`usage io`로 내 현황을 보실 수 있습니다."),
            "the rule's own check, last",
        )
        self.assertIn("2번째로 많이 쓴 잡이 `11`", io_report.dm_text(item, 1, ""))


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

            def post(self, channel, text, blocks=None, thread_ts=None, cls=None):
                assert not str(channel).startswith("#"), "nothing in a channel"
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

    def test_the_top_jobs_users_are_told_once_then_when_due_and_the_node_recorded(self):
        """Decision 12 (2026-11-28): the people by DM; the node and its users go in the
        status channel's hourly line (io.json "recent"), nothing in the admin channel."""
        with tempfile.TemporaryDirectory() as d:
            posted, error, saved = self.run_io(Prom(), d)
            self.assertIsNone(error)
            self.assertEqual(sorted(p[0] for p in posted), ["UALICE", "UBOB"], "root's job: no DM")
            self.assertIn("가장 많이 쓴 잡", dict(posted)["UALICE"])
            self.assertEqual(len(saved["told"]), 2)
            [node] = saved["recent"].values()
            self.assertEqual((node["host"], node["users"], node["t"]), ("n1", ["alice", "bob", "root"], NOW))
            with mock.patch.dict(self.CFG, state_file=f"{d}/state.json"):
                self.assertEqual(report.io_recent(self.CFG, NOW - 3600), [("n1", ["alice", "bob", "root"])])
                self.assertEqual(report.io_recent(self.CFG, NOW + 1), [], "only what was hot since")
            again, _, _ = self.run_io(Prom(), d, now=NOW + 3600)
            self.assertEqual(again, [], "within io_repeat_hours")
            later, _, saved = self.run_io(Prom(), d, now=NOW + 7 * 3600)
            self.assertEqual(len(later), 2)
            self.assertEqual(len(saved["recent"]), 1, "one record per hot node")

    def test_once_it_cools_the_next_hot_spell_is_told_at_once(self):
        with tempfile.TemporaryDirectory() as d:
            self.run_io(Prom(), d)
            _, _, saved = self.run_io(Prom(hot=False), d, now=NOW + 600)
            self.assertEqual(saved["told"], {})
            posted, _, _ = self.run_io(Prom(), d, now=NOW + 1200)
            self.assertEqual(len(posted), 2)

    def test_one_failed_dm_does_not_stop_the_others(self):
        with tempfile.TemporaryDirectory() as d:
            posted, error, saved = self.run_io(Prom(), d, fail={"UBOB"})
            self.assertIn("notifying bob", error)
            self.assertEqual(sorted(p[0] for p in posted), ["UALICE"])
            self.assertNotIn(io_report.user_key({"host": "n1", "mount": "/home", "direction": "write"}, "bob"),
                             saved["told"])  # fmt: skip

    def test_a_preview_posts_the_summary_there_and_records_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            posted, _, saved = self.run_io(Prom(), d, to="UME")
            self.assertEqual([p[0] for p in posted], ["UME", "UME"], "each DM, previewed")
            self.assertIsNone(saved)

    def test_no_measured_mount_does_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            prom = Prom()
            with mock.patch.dict(self.CFG, io_mounts={}):
                posted, _, _ = self.run_io(prom, d)
            self.assertEqual((posted, prom.asked), ([], []))


if __name__ == "__main__":
    unittest.main()
