"""Unit tests for roles/slurm_usage_report/files/slurm_usage_report.py (no cluster needed)."""
import sys
import unittest
from pathlib import Path

ROLES = Path(__file__).resolve().parents[2] / "roles"
sys.path[:0] = [str(ROLES / "cluster_tools_lib/files"), str(ROLES / "slurm_usage_report/files")]
import slurm_usage_report as report  # noqa: E402


NOW = 1_800_000_000


def unset():
    return {"set": False, "infinite": False, "number": 0}


def value(n):
    return {"set": True, "infinite": False, "number": n}


def job(jid, user, state="RUNNING", nodes="", gres=(), name="train", **extra):
    """One entry of `scontrol -d --json show job`, as Slurm 25.11 prints it."""
    return {"job_id": jid, "name": name, "user_name": user, "partition": "a10", "qos": "normal",
            "job_state": [state], "nodes": nodes, "gres_detail": list(gres),
            "start_time": value(NOW - 3 * 3600), "submit_time": value(NOW - 7200),
            "array_job_id": value(0), "array_task_id": unset(), "state_reason": "None",
            "tres_req_str": "cpu=4,mem=16G,node=1", **extra}


def node(name, gres, state=("IDLE",), reason="", user="", changed=0):
    return {"name": name, "gres": gres, "state": list(state), "reason": reason,
            "reason_set_by_user": user, "reason_changed_at": value(changed) if changed else unset()}


class ParseTests(unittest.TestCase):
    def test_typed_typeless_array_and_multinode_jobs(self):
        data = {"jobs": [
            job(1, "alice", nodes="gpu1", gres=["gpu:a10:2(IDX:0-1)"]),
            job(2, "bob", nodes="gpu3", gres=["gpu:3(IDX:0,2-3)"], array_job_id=value(10),
                array_task_id=value(7)),
            job(3, "carol", nodes="gpu[1-2]", gres=["gpu:a10:1(IDX:5)", "gpu:a10:1(IDX:6)"]),
            job(5, "erin", nodes="gpu4", gres=["gpu:a10:1(IDX:0)"], array_job_id=value(20),
                array_task_id=value(0)),
            job(4, "dave", state="PENDING"),
        ]}
        running, _ = report.parse_jobs(data, NOW)
        jobs = {j["id"]: j for j in running}
        self.assertEqual(sorted(jobs), ["1", "2", "3", "5"])
        self.assertEqual(jobs["1"]["gpus"], [("gpu1", 0), ("gpu1", 1)])
        self.assertEqual(jobs["1"]["gpu_type"], "a10")
        self.assertEqual(jobs["2"]["gpus"], [("gpu3", 0), ("gpu3", 2), ("gpu3", 3)])
        self.assertEqual(jobs["2"]["label"], "10_7")
        self.assertEqual(jobs["5"]["label"], "20_0")
        self.assertEqual(jobs["1"]["label"], "1")
        self.assertEqual(jobs["3"]["gpus"], [("gpu1", 5), ("gpu2", 6)])
        self.assertEqual(jobs["1"]["runtime"], 3 * 3600)

    def test_job_name_cannot_pose_as_other_fields(self):
        hostile = "x UserId=alice(1) QOS=high Nodes=gpu9 CPU_IDs=0 Mem=1 GRES=gpu:a10:8(IDX:0-7)"
        running, _ = report.parse_jobs({"jobs": [job(7, "mallory", nodes="gpu1",
                                                     gres=["gpu:a10:1(IDX:3)"], name=hostile)]}, NOW)
        self.assertEqual(running[0]["user"], "mallory")
        self.assertEqual(running[0]["qos"], "normal")
        self.assertEqual(running[0]["gpus"], [("gpu1", 3)])

    def test_mismatched_gres_detail_attributes_nothing(self):
        running, _ = report.parse_jobs({"jobs": [job(8, "u", nodes="gpu[1-2]",
                                                     gres=["gpu:a10:1(IDX:0)"])]}, NOW)
        self.assertEqual(running[0]["gpus"], [])

    def test_pending_counts_total_requested_gpus(self):
        data = {"jobs": [
            job(11, "alice", state="PENDING", state_reason="Priority",
                tres_req_str="cpu=8,node=2,billing=8,gres/gpu=8,gres/gpu:a10=8"),
            job(12, "alice", state="PENDING", state_reason="Resources", submit_time=value(NOW - 600)),
        ]}
        _, pending = report.parse_jobs(data, NOW)
        self.assertEqual((pending["alice"]["jobs"], pending["alice"]["gpus"]), (2, 8))
        self.assertEqual(pending["alice"]["wait"], 7200)
        self.assertEqual(pending["alice"]["reasons"], report.Counter({"Priority": 1, "Resources": 1}))

    def test_nodes_and_unavailable_states(self):
        data = {"nodes": [
            node("gpu1", "gpu:a10:8(S:0-1)"),
            node("gpu2", "gpu:a30:7(S:0-1)", ("IDLE", "DRAIN"), "maint", "root", NOW),
            node("gpu3", "gpu:a30:8(S:0-1)", ("MIXED", "DRAIN"), "fan", "root", NOW),
            node("gpu4", "gpu:a10:4(S:0-1)", ("DOWN", "NOT_RESPONDING"), "Not responding", "slurm", NOW),
            node("cpu1", ""),
        ]}
        gpus, unavailable = report.parse_nodes(data)
        self.assertEqual(gpus, {"gpu1": 8, "gpu2": 7, "gpu3": 8, "gpu4": 4, "cpu1": 0})
        self.assertEqual([(n, s, u, r) for n, s, _, u, r in unavailable],
                         [("gpu2", "drained", "root", "maint"), ("gpu3", "draining", "root", "fan"),
                          ("gpu4", "down", "slurm", "Not responding")])
        self.assertEqual(unavailable[0][2], report.datetime.fromtimestamp(NOW).isoformat(timespec="seconds"))

    def test_duration(self):
        self.assertEqual(report.duration(93784), "1-2:03:04")
        self.assertEqual(report.duration(306.9), "0:05:06")


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
