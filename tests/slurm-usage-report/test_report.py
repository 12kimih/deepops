"""Unit tests for the Slurm usage report, clustertools.usage_report (no cluster needed)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROLES = Path(__file__).resolve().parents[2] / "roles"
sys.path.insert(0, str(ROLES / "cluster_tools_lib/files"))
from clustertools import usage_report as report  # noqa: E402


NOW = 1_800_000_000


def unset():
    return {"set": False, "infinite": False, "number": 0}


def value(n):
    return {"set": True, "infinite": False, "number": n}


def job(jid, user, state="RUNNING", nodes="", gres=(), name="train", **extra):
    """One entry of `scontrol -d --json show job`, as Slurm 25.11 prints it."""
    return {
        "job_id": jid,
        "name": name,
        "user_name": user,
        "partition": "a10",
        "qos": "normal",
        "job_state": [state],
        "nodes": nodes,
        "gres_detail": list(gres),
        "start_time": value(NOW - 3 * 3600),
        "submit_time": value(NOW - 7200),
        "array_job_id": value(0),
        "array_task_id": unset(),
        "state_reason": "None",
        "tres_req_str": "cpu=4,mem=16G,node=1",
        **extra,
    }


def node(name, gres, state=("IDLE",), reason="", user="", changed=0):
    return {
        "name": name,
        "gres": gres,
        "state": list(state),
        "reason": reason,
        "reason_set_by_user": user,
        "reason_changed_at": value(changed) if changed else unset(),
    }


class ParseTests(unittest.TestCase):
    def test_typed_typeless_array_and_multinode_jobs(self):
        data = {
            "jobs": [
                job(1, "alice", nodes="gpu1", gres=["gpu:a10:2(IDX:0-1)"]),
                job(
                    2,
                    "bob",
                    nodes="gpu3",
                    gres=["gpu:3(IDX:0,2-3)"],
                    array_job_id=value(10),
                    array_task_id=value(7),
                ),
                job(3, "carol", nodes="gpu[1-2]", gres=["gpu:a10:1(IDX:5)", "gpu:a10:1(IDX:6)"]),
                job(
                    5,
                    "erin",
                    nodes="gpu4",
                    gres=["gpu:a10:1(IDX:0)"],
                    array_job_id=value(20),
                    array_task_id=value(0),
                ),
                job(4, "dave", state="PENDING"),
            ]
        }
        running, _ = report.parse_jobs(data, NOW)
        jobs = {j["id"]: j for j in running}
        self.assertEqual(sorted(jobs), ["1", "2", "3", "5"])
        self.assertEqual(jobs["1"]["gpus"], [("gpu1", 0), ("gpu1", 1)])
        self.assertEqual(jobs["1"]["gpu_type"], "a10")
        self.assertEqual(jobs["2"]["gpus"], [("gpu3", 0), ("gpu3", 2), ("gpu3", 3)])
        self.assertEqual(jobs["2"]["gpu_type"], "")  # untyped: its count is not a type
        self.assertEqual(jobs["2"]["label"], "10_7")
        self.assertEqual(jobs["5"]["label"], "20_0")
        self.assertEqual(jobs["1"]["label"], "1")
        self.assertEqual(jobs["3"]["gpus"], [("gpu1", 5), ("gpu2", 6)])
        self.assertEqual(jobs["1"]["runtime"], 3 * 3600)

    def test_job_name_cannot_pose_as_other_fields(self):
        hostile = "x UserId=alice(1) QOS=high Nodes=gpu9 CPU_IDs=0 Mem=1 GRES=gpu:a10:8(IDX:0-7)"
        running, _ = report.parse_jobs(
            {"jobs": [job(7, "mallory", nodes="gpu1", gres=["gpu:a10:1(IDX:3)"], name=hostile)]}, NOW
        )
        self.assertEqual(running[0]["user"], "mallory")
        self.assertEqual(running[0]["qos"], "normal")
        self.assertEqual(running[0]["gpus"], [("gpu1", 3)])

    def test_mismatched_gres_detail_attributes_nothing(self):
        running, _ = report.parse_jobs(
            {"jobs": [job(8, "u", nodes="gpu[1-2]", gres=["gpu:a10:1(IDX:0)"])]}, NOW
        )
        self.assertEqual(running[0]["gpus"], [])

    def test_pending_counts_total_requested_gpus(self):
        data = {
            "jobs": [
                job(
                    11,
                    "alice",
                    state="PENDING",
                    state_reason="Priority",
                    tres_req_str="cpu=8,node=2,billing=8,gres/gpu=8,gres/gpu:a10=8",
                ),
                job(12, "alice", state="PENDING", state_reason="Resources", submit_time=value(NOW - 600)),
            ]
        }
        _, pending = report.parse_jobs(data, NOW)
        self.assertEqual((pending["alice"]["jobs"], pending["alice"]["gpus"]), (2, 8))
        self.assertEqual(pending["alice"]["wait"], 7200)
        self.assertEqual(pending["alice"]["reasons"], report.Counter({"Priority": 1, "Resources": 1}))

    def test_a_pending_array_record_counts_every_task(self):
        record = job(13, "bob", state="PENDING", state_reason="Priority", tres_req_str="cpu=1,node=1,gres/gpu=2",
                     array_job_id=value(13), array_task_string="0-9:2%1")  # fmt: skip
        _, pending = report.parse_jobs({"jobs": [record]}, NOW)
        self.assertEqual((pending["bob"]["jobs"], pending["bob"]["gpus"]), (5, 10))
        self.assertEqual(pending["bob"]["reasons"], report.Counter({"Priority": 5}))

    def test_nodes_and_unavailable_states(self):
        data = {
            "nodes": [
                node("gpu1", "gpu:a10:8(S:0-1)"),
                node("gpu2", "gpu:a30:7(S:0-1)", ("IDLE", "DRAIN"), "maint", "root", NOW),
                node("gpu3", "gpu:a30:8(S:0-1)", ("MIXED", "DRAIN"), "fan", "root", NOW),
                node("gpu4", "gpu:a10:4(S:0-1)", ("DOWN", "NOT_RESPONDING"), "Not responding", "slurm", NOW),
                node("cpu1", ""),
            ]
        }
        gpus, unavailable = report.parse_nodes(data)
        self.assertEqual(gpus, {"gpu1": 8, "gpu2": 7, "gpu3": 8, "gpu4": 4, "cpu1": 0})
        self.assertEqual(
            [(n, s, u, r) for n, s, _, u, r in unavailable],
            [
                ("gpu2", "drained", "root", "maint"),
                ("gpu3", "draining", "root", "fan"),
                ("gpu4", "down", "slurm", "Not responding"),
            ],
        )
        self.assertEqual(unavailable[0][2], report.datetime.fromtimestamp(NOW).isoformat(timespec="seconds"))

    def test_duration(self):
        self.assertEqual(report.duration(93784), "1-2:03:04")
        self.assertEqual(report.duration(306.9), "0:05:06")


class IdleTests(unittest.TestCase):
    def job(self, jid, runtime, gpus):
        return {
            "id": jid,
            "label": jid,
            "user": "u",
            "runtime": runtime,
            "gpus": gpus,
            "gpu_type": "a10",
            "qos": "normal",
            "partition": "p",
        }

    def test_idle_needs_full_window_and_data_for_every_gpu(self):
        gpu = {
            "util_max": {("n", 0): 0, ("n", 1): 0, ("n", 2): 50},
            "util": {("n", 0): 0, ("n", 1): 0, ("n", 2): 40},
            "mem_gib": {},
        }
        jobs = [
            self.job("all-idle", 7200, [("n", 0), ("n", 1)]),
            self.job("too-young", 60, [("n", 0)]),
            self.job("no-data", 7200, [("n", 0), ("n", 9)]),
            self.job("partly", 7200, [("n", 0), ("n", 2)]),
        ]
        idle = report.analyze(jobs, gpu, window=7200, threshold=0)
        self.assertEqual([j["id"] for j in idle], ["all-idle"])
        self.assertEqual(jobs[3]["idle_gpus"], 1)

    def test_unknown_gpu_memory_is_not_zero(self):
        gpu = {"util_max": {}, "util": {}, "mem_gib": {("n", 0): 12.0}}
        jobs = [
            self.job("known", 60, [("n", 0)]),
            self.job("partly", 60, [("n", 0), ("n", 1)]),
            self.job("unknown", 60, [("n", 9)]),
        ]
        report.analyze(jobs, gpu, window=7200, threshold=0)
        self.assertEqual([report.gib(j) for j in jobs], ["12G", "12G+", "n/a"])

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
                jobs.append(
                    {
                        "id": f"{u}{k}",
                        "label": f"{u}{k}",
                        "user": f"user{u}",
                        "runtime": 3600 * k,
                        "gpus": [(f"gpu{k}", 0)],
                        "gpu_type": "a10",
                        "qos": "normal",
                        "partition": "p",
                        "util": 50.0,
                        "mem_gib": 10.0,
                        "mem_known": 1,
                        "idle": False,
                        "idle_gpus": 0,
                    }
                )
        data = {
            "jobs": jobs,
            "idle": [],
            "total_gpus": 1000,
            "reasons": [],
            "now": 0,
            # Every section over Slack's limit: pending users, drained nodes, idle jobs.
            "pending": {
                f"user{u}": {"jobs": 1, "gpus": 1, "reasons": report.Counter({"Priority": 1}), "wait": 60}
                for u in range(200)
            },
        }
        data["reasons"] = [
            (f"gpu{k}", "drain", "2026-01-01T00:00:00", "root", "maintenance window") for k in range(100)
        ]
        data["idle"] = [dict(j, notify=False) for j in jobs[:200]]
        cfg = {"idle_util_threshold": 0, "idle_hours": 2, "notify": ["mention"], "dashboard_url": ""}
        (text, summary), thread = report.render(data, cfg, lambda user, mention: user)
        self.assertTrue(text)
        for _, blocks in [("summary", summary)] + thread:
            self.assertLessEqual(len(blocks), 50)
            for b in blocks:
                self.assertLessEqual(len(b.get("text", {}).get("text", "")), 3000)
        self.assertLessEqual(len(summary[0]["text"]["text"]), 150)

    def test_a_long_section_is_cut_at_a_line_and_closes_its_code_block(self):
        text = "head\n```\n" + "\n".join(f"row {i:04d} " + "x" * 40 for i in range(200)) + "\n```"
        cut = report.section(text)["text"]["text"]
        self.assertLessEqual(len(cut), 3000)
        self.assertEqual(cut.count("```") % 2, 0)
        kept = sum(1 for ln in cut.splitlines() if ln.startswith("row "))
        self.assertIn(f"... {200 - kept} more lines", cut)
        self.assertEqual(report.section("short")["text"]["text"], "short")

    def test_bar(self):
        self.assertEqual(report.bar(100), "█" * 10 + " 100%")
        self.assertEqual(report.bar(0), "░" * 10 + "   0%")
        self.assertTrue(report.bar(None).startswith("n/a"))


class RunTests(unittest.TestCase):
    """report_once: a failure part-way still records what this run did."""

    def test_state_is_saved_when_a_notification_fails(self):
        idle = [{"id": i, "label": i, "user": u, "gpus": [("gpu1", 0)], "gpu_type": "a10", "runtime": 9000,
                 "qos": "normal", "partition": "p", "util": 0.0, "mem_gib": 1.0, "mem_known": 1, "idle": True,
                 "idle_gpus": 1} for i, u in (("1", "alice"), ("2", "bob"))]  # fmt: skip
        posted = []

        class Slack:
            token = "t"

            def __init__(self, *_):
                pass

            def channel(self, target):
                return target

            def post(self, channel, text, blocks=None, thread_ts=None):
                if channel == "UBOB":
                    raise RuntimeError("Slack chat.postMessage failed: channel_not_found")
                posted.append(channel)
                return "ts"

        with tempfile.TemporaryDirectory() as d:
            cfg = {"channel": "#gpu", "state_file": f"{d}/state.json", "idle_hours": 2, "idle_util_threshold": 1,
                   "notify": ["dm"], "notify_repeat_hours": 24, "cancel_after_hours": 0, "prometheus_url": "",
                   "slack_token": "t", "user_map": {"alice": "UALICE", "bob": "UBOB"}, "email_domain": "",
                   "dm_text": "{job}", "dashboard_url": ""}  # fmt: skip
            Path(f"{d}/config.json").write_text(json.dumps(cfg))
            slurm = {"jobs": idle, "nodes": {"gpu1": 1}, "reasons": [], "pending": {}}
            with mock.patch.object(sys, "argv", ["r", "--config", f"{d}/config.json"]), \
                    mock.patch.object(report, "collect_slurm", lambda now: dict(slurm, jobs=[dict(j) for j in idle])), \
                    mock.patch.object(report, "collect_gpus", lambda *_: {}), \
                    mock.patch.object(report, "analyze", lambda jobs, *_: jobs), \
                    mock.patch.object(report, "Slack", Slack):  # fmt: skip
                self.assertEqual(report.main(), 1)
                state = json.loads(Path(f"{d}/state.json").read_text())
                # alice was told and is not told again next run; bob was not, so he is retried.
                self.assertIsNotNone(state["idle"]["1"]["notified"])
                self.assertIsNone(state["idle"]["2"]["notified"])
                self.assertEqual(posted.count("UALICE"), 1)
                report.main()
                self.assertEqual(posted.count("UALICE"), 1)
                self.assertEqual(Path(f"{d}/state.json").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
