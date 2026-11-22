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
        # squeue's form, as sq shows the same jobs
        self.assertEqual(report.duration(93784), "1-02:03:04")
        self.assertEqual(report.duration(306.9), "5:06")


class IdleTests(unittest.TestCase):
    def job(self, jid, runtime, gpus):
        return {
            "id": jid,
            "label": jid,
            "user": "u",
            "running": True,
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
            dict(self.job("suspended", 7200, [("n", 0)]), running=False),
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

    def test_idle_since_is_kept_and_forgotten_when_no_longer_idle(self):
        state, j = {}, self.job("1", 7200, [("n", 0)])
        report.track_idle([j], state, now=10000, window=7200)
        self.assertEqual(state["1"], {"since": 2800, "notified": None})
        self.assertIs(j["state"], state["1"])
        report.track_idle([j], state, now=20000, window=7200)
        self.assertEqual(state["1"]["since"], 2800, "the first time it turned idle stays")
        report.track_idle([], state, now=30000, window=7200)
        self.assertEqual(state, {})

    def test_a_persons_jobs_are_one_group_most_gpus_first(self):
        a, b = self.job("a", 7200, [("n", 0)]), self.job("b", 7200, [("n", 1), ("n", 2)])
        c = dict(self.job("c", 7200, [("n", 3)]), user="v")
        self.assertEqual({u: [j["id"] for j in js] for u, js in report.by_owner([a, b, c]).items()},
                         {"u": ["b", "a"], "v": ["c"]})  # fmt: skip
        self.assertEqual(report.job_lines([a]), "- `a` (1 GPU, running 2:00:00)")


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
        data["idle"] = jobs[:200]
        data["due"] = []
        cfg = {"idle_util_threshold": 0, "idle_hours": 2, "notify": ["mention"], "dashboard_url": ""}
        (text, summary), thread = report.render(data, cfg, lambda user, mention: user)
        self.assertTrue(text)
        for _, blocks in [("summary", summary)] + thread:
            self.assertLessEqual(len(blocks), 50)
            for b in blocks:
                self.assertLessEqual(len(b.get("text", {}).get("text", "")), 3000)
        self.assertLessEqual(len(summary[0]["text"]["text"]), 150)


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
                   "dm_text": "{jobs}", "admin_channel": "#admin",
                   "repeat_idle_gpu_hours": 50, "repeat_idle_share": 0.5, "repeat_weeks": 2,
                   "cancel_warn_hours": 1, "cancel_warn_text": "{jobs} {hours}",
                   "storage_top": 10, "storage_notify": ["mention"], "storage_repeat_days": 7,
                   "people": [], "holds_on": False, "holds_from": 0}  # fmt: skip
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


class GpuIndexTests(unittest.TestCase):
    def test_keyed_on_the_device_slurm_counts_not_the_nvml_index(self):
        # NVML index 0 is /dev/nvidia1 here: Slurm's IDX 1 must read its figure. The
        # query goes through the minor-number mapping; its device label is Slurm's.
        prom = report.Prometheus("http://x")
        asked = []
        rows = [{"metric": {"host": "n", "gpu": "0", "device": "nvidia1"}, "value": [0, "70"]},
                {"metric": {"host": "n", "gpu": "1", "device": "nvidia0"}, "value": [0, "0"]},
                {"metric": {"host": "n", "gpu": "2"}, "value": [0, "5"]}]  # fmt: skip
        with mock.patch.object(prom, "query", lambda expr: asked.append(expr) or rows):
            self.assertEqual(report.per_gpu(prom, "q"), {("n", 1): 70.0, ("n", 0): 0.0})
        self.assertIn("group_left (device) host_gpu:dcgm_slurm_device:info", asked[0])


class ConfigTests(unittest.TestCase):
    BASE = {"slack_token": "t", "channel": "#c", "prometheus_url": "", "idle_hours": 2,
            "idle_util_threshold": 0, "notify": ["mention"], "notify_repeat_hours": 6,
            "dm_text": "{jobs} {count} {hours}", "cancel_after_hours": 0, "user_map": {},
            "email_domain": "", "state_file": "/nonexistent", "admin_channel": "#admin",
            "repeat_idle_gpu_hours": 50, "repeat_idle_share": 0.5, "repeat_weeks": 2,
            "cancel_warn_hours": 1, "cancel_warn_text": "{jobs} {hours}",
            "storage_top": 10, "storage_notify": ["mention", "dm"], "storage_repeat_days": 7,
            "people": ["alice"], "holds_on": False, "holds_from": 0}  # fmt: skip

    def load(self, **change):
        with tempfile.TemporaryDirectory() as d:
            Path(f"{d}/c.json").write_text(json.dumps(dict(self.BASE, **change)))
            return report.load_config(f"{d}/c.json")

    def test_a_good_config_loads(self):
        self.assertEqual(self.load()["channel"], "#c")
        self.assertEqual(self.load(cancel_after_hours=24)["cancel_after_hours"], 24)

    def test_each_mistake_is_named_before_anything_runs(self):
        for change, word in (({"dm_text": "{user}"}, "dm_text"), ({"dm_text": "{"}, "dm_text"),
                             ({"notify": ["email"]}, "notify"), ({"idle_hours": "2"}, "idle_hours"),
                             ({"cancel_after_hours": 1}, "exceed"),
                             ({"cancel_after_hours": 12, "cancel_warn_hours": 10}, "cancel_warn_hours"),
                             ({"cancel_after_hours": 12, "cancel_warn_text": "{user}"}, "cancel_warn_text"),
                             ({"cancel_after_hours": 24, "notify": []}, "notify"),
                             ({"channel": None}, "channel"), ({"storage_notify": ["email"]}, "storage_notify"),
                             ({"storage_top": 0}, "storage_top"),
                             ({"storage_repeat_days": -1}, "storage_repeat_days"), ({"people": "alice"}, "people")):  # fmt: skip
            with self.subTest(change=change), self.assertRaisesRegex(report.ConfigError, word):
                self.load(**change)

    def test_a_damaged_state_file_is_a_fresh_start(self):
        with tempfile.TemporaryDirectory() as d:
            for text in ("[1]", "{bad", '{"idle": [], "users": 3}',
                         '{"idle": {"1": {"since": "x"}, "2": {"since": 5, "notified": null},'
                         ' "3": {"since": 5}, "4": {"since": true, "notified": null}}}'):  # fmt: skip
                Path(f"{d}/s").write_text(text)
                state = report.load_state(f"{d}/s")
                self.assertEqual(set(state), {"idle", "told", "users"})
                self.assertEqual(set(state["idle"]) - {"2"}, set())


class CancelTests(unittest.TestCase):
    """Cancelling: any job, batch or interactive, and only once its owner was told and
    warned in earlier runs; one warning per person for all their jobs about to go."""

    CFG = {"notify": ["mention"], "cancel_after_hours": 24, "channel": "#c", "idle_hours": 2,
           "cancel_warn_hours": 1, "cancel_warn_text": "{count}: {jobs} in {hours}h", "dm_text": "{jobs}"}  # fmt: skip

    def job(self, jid, notified, warned=None, user="u"):
        state = {"since": NOW - 30 * 3600, "notified": notified}
        if warned is not None:
            state["warned"] = warned
        return {"id": jid, "label": jid, "user": user, "gpus": [("n", 0)], "runtime": 9, "state": state}

    def run_act(self, jobs, due=(), member="UU", **cfg):
        self.slack = mock.Mock()
        self.slack.post.return_value = "ts"
        self.told = {}
        report_data = {"idle": jobs, "due": list(due), "told": self.told}
        with mock.patch.object(report, "post_report", lambda *a: ("#c", "ts")), \
                mock.patch.object(report, "slack_id", lambda *a: member), mock.patch.object(report, "run") as run, \
                mock.patch("sys.stderr"):  # fmt: skip
            report.act(report_data, dict(self.CFG, **cfg), lambda u, m: u, self.slack, {}, NOW)
        return [c.args[1] for c in run.call_args_list]

    def test_warned_first_then_cancelled_in_a_later_run(self):
        j = self.job("7", NOW - 3600)
        self.assertEqual(self.run_act([j]), [], "told but not yet warned: warned now, not cancelled")
        self.assertEqual(j["state"]["warned"], NOW)
        self.assertEqual(self.run_act([self.job("7", NOW - 3600, warned=NOW - 600)]), ["7"])

    def test_one_warning_per_person_for_all_their_jobs(self):
        jobs = [self.job("7", NOW - 3600), self.job("8", NOW - 3600), self.job("9", NOW - 3600, user="v")]
        self.run_act(jobs)
        texts = [c.args[1] for c in self.slack.post.call_args_list]
        self.assertEqual(len(texts), 2, "one DM to u, one to v")
        self.assertTrue(texts[0].startswith("2: - `7`"), texts[0])

    def test_batch_and_interactive_alike(self):
        batch = dict(self.job("7", NOW - 3600, warned=NOW - 600), interactive=False)
        self.assertEqual(self.run_act([batch]), ["7"])

    def test_told_once_per_person_and_every_job_of_theirs_counts_as_told(self):
        jobs = [self.job("7", None), self.job("8", None)]
        self.run_act(jobs, due=["u"], notify=["dm"], cancel_after_hours=0)
        self.assertEqual(self.slack.post.call_count, 1, "one DM for both jobs")
        self.assertEqual(self.told, {"u": NOW})
        self.assertEqual([j["state"]["notified"] for j in jobs], [NOW, NOW])

    def test_an_owner_without_a_slack_member_is_not_counted_as_told(self):
        j = self.job("7", None)
        self.run_act([j], due=["u"], member="", notify=["dm"])
        self.assertIsNone(j["state"]["notified"])
        self.assertEqual(self.told, {})

    def test_cancelled_only_after_the_owner_was_told_in_an_earlier_run(self):
        self.assertEqual(self.run_act([self.job("7", None, warned=NOW - 600)]), [])  # never told
        self.assertEqual(
            self.run_act([self.job("7", NOW - 3600)], cancel_warn_hours=0), ["7"]
        )  # no warning step


class WeeklyTests(unittest.TestCase):
    """The weekly summary: GPU-hours held and idle per person, idlest first and the
    idlest mentioned; a person over both thresholds for repeat_weeks weeks running is
    reported to the administrators, with their numbers and what to do."""

    CFG = {"repeat_weeks": 2, "repeat_idle_gpu_hours": 50, "repeat_idle_share": 0.5}

    class Prom:
        def query(self, expr):
            idle = "<=" in expr
            values = {"alice": 40 if idle else 100, "bob": 90 if idle else 120, "carol": 0 if idle else 30}
            return [{"metric": {"user": u}, "value": [0, str(v)]} for u, v in values.items()]

    def test_collect_and_render_idlest_first(self):
        rows = report.collect_week(self.Prom(), 0)
        self.assertEqual([r["user"] for r in rows], ["bob", "alice", "carol"])
        text, blocks = report.render_week(rows, lambda u, m: f"@{u}" if m else u, top=1)
        self.assertIn("250 GPU-hours held, 130 idle (52%)", text)
        body = " ".join(b["text"]["text"] for b in blocks if b["type"] == "section")
        self.assertIn("@bob: 90 of 120 GPU-hours idle (75%)", body)
        self.assertIn("alice: 40 of 100", body)
        self.assertNotIn("@alice", body)

    def test_a_streak_of_heavy_weeks_is_reported_and_a_rerun_replaces_its_week(self):
        weeks = {}
        heavy = [{"user": "bob", "held": 120.0, "idle": 90.0}]
        report.remember_week(weeks, heavy, 0)
        self.assertEqual(report.repeaters(weeks, self.CFG), [])
        report.remember_week(weeks, heavy, 3 * 86400)  # run again by hand the same week
        self.assertEqual(len(weeks["bob"]), 1)
        report.remember_week(weeks, heavy, 7 * 86400)
        self.assertEqual(report.repeaters(weeks, self.CFG), ["bob"])
        report.remember_week(weeks, [], 14 * 86400)  # a week without jobs breaks it
        self.assertEqual(report.repeaters(weeks, self.CFG), [])
        text, blocks = report.render_repeaters(["bob"], {"bob": weeks["bob"][:2]}, [], self.CFG)
        self.assertIn("bob", text)
        context = blocks[-1]["elements"][0]["text"]
        self.assertIn("What to do", context)
        self.assertIn("50 idle GPU-hours and 50%", context)


if __name__ == "__main__":
    unittest.main()
