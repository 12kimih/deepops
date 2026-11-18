"""Unit tests for roles/slurm_usage_report/files/slurm-usage-report.py (no cluster needed)."""
import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "roles/slurm_usage_report/files/slurm-usage-report.py"
spec = importlib.util.spec_from_file_location("report", SCRIPT)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def job_line(jid, user, state="RUNNING", runtime="03:00:00", details="", extra=""):
    return (f"JobId={jid} {extra}JobName=train UserId={user}(1000) GroupId=lab(1000) "
            f"Priority=1 Partition=a10 QOS=normal JobState={state} Reason=None RunTime={runtime} "
            f"NumNodes=1 {details} TresPerNode=gres/gpu:4")


class ParseTests(unittest.TestCase):
    def setUp(self):
        report._hostnames.clear()
        report.run = lambda *cmd: {"gpu[1-2]": "gpu1\ngpu2\n"}[cmd[-1]]

    def test_typed_typeless_array_and_multinode_jobs(self):
        text = "\n".join([
            job_line(1, "alice", details="Nodes=gpu1 CPU_IDs=0-3 Mem=100 GRES=gpu:a10:2(IDX:0-1)"),
            job_line(2, "bob", details="Nodes=gpu3 CPU_IDs=0-3 Mem=100 GRES=gpu:3(IDX:0,2-3)",
                     extra="ArrayJobId=10 ArrayTaskId=7 "),
            job_line(3, "carol", details="Nodes=gpu[1-2] CPU_IDs=0-3 Mem=100 GRES=gpu:a10:1(IDX:5)"),
            job_line(4, "dave", state="PENDING"),
        ])
        jobs = {j["id"]: j for j in report.parse_jobs(text)}
        self.assertEqual(sorted(jobs), ["1", "2", "3"])
        self.assertEqual(jobs["1"]["gpus"], [("gpu1", 0), ("gpu1", 1)])
        self.assertEqual(jobs["1"]["gpu_type"], "a10")
        self.assertEqual(jobs["2"]["gpus"], [("gpu3", 0), ("gpu3", 2), ("gpu3", 3)])
        self.assertEqual(jobs["2"]["label"], "10_7")
        self.assertEqual(jobs["3"]["gpus"], [("gpu1", 5), ("gpu2", 5)])
        self.assertEqual(jobs["1"]["runtime"], 3 * 3600)

    def test_nodes_count_once_across_partitions(self):
        text = "gpu1|gpu:a10:8(S:0-1)\ngpu1|gpu:a10:8(S:0-1)\ngpu2|gpu:a30:7(S:0-1)\nmaster|(null)"
        self.assertEqual(report.parse_nodes(text), {"gpu1": 8, "gpu2": 7, "master": 0})

    def test_pending_gpus_scale_with_nodes(self):
        text = ("alice|2|gres/gpu:a10:4|2026-01-01T00:00:00|Priority\n"
                "alice|1|N/A|2026-01-01T01:00:00|Resources, extra words\n")
        p = report.parse_pending(text, now=report.datetime.fromisoformat("2026-01-01T02:00:00").timestamp())
        self.assertEqual((p["alice"]["jobs"], p["alice"]["gpus"]), (2, 8))
        self.assertEqual(p["alice"]["wait"], 7200)

    def test_runtime_formats(self):
        self.assertEqual(report.seconds("1-02:03:04"), 93784)
        self.assertEqual(report.seconds("05:06"), 306)
        self.assertEqual(report.duration(93784), "1-2:03:04")


class IdleTests(unittest.TestCase):
    def job(self, jid, runtime, gpus):
        return {"id": jid, "label": jid, "user": "u", "runtime": runtime, "gpus": gpus,
                "gpu_type": "a10", "qos": "normal", "partition": "p"}

    def test_idle_needs_full_window_and_data_for_every_gpu(self):
        gpu = {"util_max": {("n", 0): 0, ("n", 1): 0, ("n", 2): 50},
               "util": {("n", 0): 0, ("n", 1): 0, ("n", 2): 40}, "mem_gib": {}}
        jobs = [self.job("all-idle", 7200, [("n", 0), ("n", 1)]),
                self.job("too-young", 60, [("n", 0)]),
                self.job("no-data", 7200, [("n", 0), ("n", 9)]),
                self.job("partly", 7200, [("n", 0), ("n", 2)])]
        idle = report.analyze(jobs, gpu, window=7200, threshold=0)
        self.assertEqual([j["id"] for j in idle], ["all-idle"])
        self.assertEqual(jobs[3]["idle_gpus"], 1)

    def test_owner_notified_once_then_after_repeat(self):
        state, j = {}, self.job("1", 7200, [("n", 0)])
        report.track_idle([j], state, now=1000, window=7200, repeat=3600, notify=["mention"])
        self.assertTrue(j["notify"])
        state["1"]["notified"] = 1000
        report.track_idle([j], state, now=2000, window=7200, repeat=3600, notify=["mention"])
        self.assertFalse(j["notify"])
        report.track_idle([j], state, now=4700, window=7200, repeat=3600, notify=["mention"])
        self.assertTrue(j["notify"])
        report.track_idle([], state, now=5000, window=7200, repeat=3600, notify=["mention"])
        self.assertEqual(state, {})

    def test_no_notification_when_disabled(self):
        j = self.job("1", 7200, [("n", 0)])
        report.track_idle([j], {}, now=1000, window=7200, repeat=3600, notify=[])
        self.assertFalse(j["notify"])


class RenderTests(unittest.TestCase):
    def test_large_cluster_stays_within_slack_limits(self):
        jobs = []
        for u in range(40):
            for k in range(25):
                jobs.append({"id": f"{u}{k}", "label": f"{u}{k}", "user": f"user{u}", "runtime": 3600 * k,
                             "gpus": [(f"gpu{k}", 0)], "gpu_type": "a10", "qos": "normal", "partition": "p",
                             "util": 50.0, "mem_gib": 10.0, "idle": False, "idle_gpus": 0})
        data = {"jobs": jobs, "idle": [], "total_gpus": 1000, "reasons": [], "now": 0,
                "pending": {f"user{u}": {"jobs": 1, "gpus": 1, "reasons": report.Counter({"Priority": 1}),
                                          "wait": 60} for u in range(40)}}
        cfg = {"idle_util_threshold": 0, "idle_hours": 2, "notify": ["mention"], "dashboard_url": ""}
        (text, summary), thread = report.render(data, cfg, lambda user, mention: user)
        self.assertTrue(text)
        for _, blocks in [("summary", summary)] + thread:
            self.assertLessEqual(len(blocks), 50)
            for b in blocks:
                self.assertLessEqual(len(b.get("text", {}).get("text", "")), 3000)
        self.assertLessEqual(len(summary[0]["text"]["text"]), 150)

    def test_bar(self):
        self.assertEqual(report.bar(100), "█" * 10 + " 100%")
        self.assertEqual(report.bar(0), "░" * 10 + "   0%")
        self.assertTrue(report.bar(None).startswith("n/a"))


if __name__ == "__main__":
    unittest.main()
