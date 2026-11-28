"""Unit tests for the Slurm usage report, cluster_tools.usage_report (no cluster needed)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROLES = Path(__file__).resolve().parents[2] / "roles"
sys.path.insert(0, str(ROLES / "cluster_tools_lib/files"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests/lib"))
from cluster_tools import rich  # noqa: E402
from cluster_tools import usage_report as report  # noqa: E402
from fake_slack import FakeDM  # noqa: E402

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
        self.assertEqual([report.gib(j) for j in jobs], ["12G", "12G+", "-"])

    def test_idle_since_is_kept_and_forgotten_when_no_longer_idle(self):
        state, j = {}, self.job("1", 7200, [("n", 0)])
        report.track_idle([j], state, now=10000, window=7200)
        self.assertEqual(state["1"], {"since": 2800, "notified": None, "seen": 10000})
        self.assertIs(j["state"], state["1"])
        report.track_idle([j], state, now=16000, window=7200)
        self.assertEqual(state["1"]["since"], 2800, "the first time it turned idle stays")
        report.track_idle([], state, now=30000, window=7200)
        self.assertEqual(state, {})

    def test_a_record_no_run_saw_for_a_window_starts_afresh(self):
        # Runs failed for longer than the window: whether the job was busy meanwhile is
        # unknown, so its old since and warning must not cancel it at once.
        state, j = {}, self.job("1", 7200, [("n", 0)])
        state["1"] = {"since": 0, "notified": 100, "warned": 200, "seen": 1000}
        report.track_idle([j], state, now=1000 + 7201, window=7200)
        self.assertEqual(state["1"], {"since": 1001, "notified": None, "seen": 8201})
        old = {"1": {"since": 0, "notified": 100}}  # a record from before "seen" existed
        report.track_idle([j], old, now=5000, window=7200)
        self.assertIsNone(old["1"]["notified"])

    def test_a_persons_jobs_are_one_group_most_gpus_first(self):
        a, b = self.job("a", 7200, [("n", 0)]), self.job("b", 7200, [("n", 1), ("n", 2)])
        c = dict(self.job("c", 7200, [("n", 3)]), user="v")
        self.assertEqual({u: [j["id"] for j in js] for u, js in report.by_owner([a, b, c]).items()},
                         {"u": ["b", "a"], "v": ["c"]})  # fmt: skip
        self.assertEqual(report.job_lines([a]), "- `a` (GPU 1장, 2:00:00 실행 중)")


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
            # Every section over Slack's limit: pending users, drained nodes, jobs leaving their GPUs idle.
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
                self.assertLessEqual(len(rich.read([b])), 3000)
        self.assertLessEqual(len(summary[0]["text"]["text"]), 150)
        # One noun for these jobs in the notification, the card and the thread (owner,
        # 2026-11-27).
        for shown in (text, rich.read(summary), rich.read(thread[0][1])):
            self.assertIn(report.IDLE_JOB, shown)
        self.assertIn(f"*{report.DOWN_TITLE} (drain, down)*", rich.read(summary))

    def test_every_pending_reason_is_shown(self):
        """The waiting jobs' reasons were cut to the two most common."""
        reasons = report.Counter({"Priority": 3, "Resources": 2, "QOSMaxGRESPerUser": 1})
        rows = rich.read(
            report.pending_section({"alice": {"jobs": 6, "gpus": 6, "reasons": reasons, "wait": 60}})
        )
        for reason in reasons:
            self.assertIn(reason, rows)

    def test_an_idle_job_leaves_everyone_in_the_per_person_table(self):
        """The per-person table lists every running GPU job, not the idle section's last
        owner's jobs (owner 2026-11-26: with one job leaving its GPUs idle, the thread showed only it)."""

        def gpu_job(jid, user, idle):
            return {"id": jid, "label": jid, "user": user, "runtime": 7200, "gpus": [("gpu1", int(jid))],
                    "gpu_type": "a10", "qos": "normal", "partition": "p", "util": 0.0 if idle else 80.0,
                    "mem_gib": 1.0, "mem_known": 1, "idle": idle, "idle_gpus": 1 if idle else 0}  # fmt: skip

        jobs = [gpu_job("1", "alice", False), gpu_job("2", "bob", True), gpu_job("3", "carol", False)]
        data = {"jobs": jobs, "idle": [jobs[1]], "total_gpus": 8, "reasons": [], "now": 0,
                "pending": {}, "due": []}  # fmt: skip
        cfg = {"idle_util_threshold": 0, "idle_hours": 2, "notify": ["mention"], "dashboard_url": ""}
        _, thread = report.render(data, cfg, lambda user, mention: user)
        body = "\n".join(rich.read(blocks) for _, blocks in thread)
        people = body[body.index("사람별 실행 중인 GPU 잡") :]
        for user in ("alice", "bob", "carol"):
            self.assertIn(user, people)
        self.assertEqual(people.count("잡 1개"), 3, "one row per person, each with their own job")


class RunTests(unittest.TestCase):
    """report_once: a failure part-way still records what this run did."""

    def test_state_is_saved_when_a_notification_fails(self):
        idle = [{"id": i, "label": i, "user": u, "gpus": [("gpu1", 0)], "gpu_type": "a10", "runtime": 9000,
                 "qos": "normal", "partition": "p", "util": 0.0, "mem_gib": 1.0, "mem_known": 1, "idle": True,
                 "idle_gpus": 1} for i, u in (("1", "alice"), ("2", "bob"))]  # fmt: skip
        posted = []

        class Slack(FakeDM):
            token = "t"

            def __init__(self, *_, **__):
                pass

            def post(self, channel, text, blocks=None, thread_ts=None):
                if channel == "UBOB":
                    raise RuntimeError("Slack chat.postMessage failed: channel_not_found")
                posted.append(channel)
                return "ts"

        with tempfile.TemporaryDirectory() as d:
            cfg = {"channel": "#gpu", "state_file": f"{d}/state.json", "idle_hours": 2, "idle_util_threshold": 1,
                   "notify": ["dm"], "notify_repeat_hours": 24, "cancel_after_hours": 0, "prometheus_url": "",
                   "slack_token": "t", "user_map": {"alice": "UALICE", "bob": "UBOB"}, "email_domain": "",
                   "dm_text": "{jobs}", "admin_channel": "#admin", "admin_mention": "",
                   "repeat_idle_gpu_hours": 50, "repeat_idle_share": 0.5, "repeat_weeks": 2,
                   "cancel_warn_hours": 1, "cancel_warn_text": "{jobs} {hours}",
                   "disk_notify": ["mention"], "disk_repeat_days": 7,
                   "people": [], "holds_on": False, "holds_from": 0, "disk_fill": 0.8, "disk_fill_mentions": 5, "stale_days": 60,
            "io_mounts": {"/home": {"read_bps": 100, "write_bps": 80}}, "io_share": 0.4, "io_minutes": 15,
            "io_top_jobs": 3, "io_repeat_hours": 6, "io_advice": "x"}  # fmt: skip
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
            "email_domain": "", "state_file": "/nonexistent", "admin_channel": "#admin", "admin_mention": "",
            "repeat_idle_gpu_hours": 50, "repeat_idle_share": 0.5, "repeat_weeks": 2,
            "cancel_warn_hours": 1, "cancel_warn_text": "{jobs} {hours}",
            "disk_notify": ["mention", "dm"], "disk_repeat_days": 7,
            "people": ["alice"], "holds_on": False, "holds_from": 0, "disk_fill": 0.8, "disk_fill_mentions": 5, "stale_days": 60,
            "io_mounts": {"/home": {"read_bps": 100, "write_bps": 80}}, "io_share": 0.4, "io_minutes": 15,
            "io_top_jobs": 3, "io_repeat_hours": 6, "io_advice": "x"}  # fmt: skip

    def load(self, **change):
        with tempfile.TemporaryDirectory() as d:
            Path(f"{d}/c.json").write_text(json.dumps(dict(self.BASE, **change)))
            return report.load_config(f"{d}/c.json")

    def test_a_config_written_before_the_io_notices_still_loads_and_tells_no_one(self):
        old = {k: v for k, v in self.BASE.items() if not k.startswith("io_")}
        with tempfile.TemporaryDirectory() as d:
            Path(f"{d}/c.json").write_text(json.dumps(old))
            cfg = report.load_config(f"{d}/c.json")
        self.assertEqual(cfg["io_mounts"], {})
        self.assertEqual(cfg["io_share"], 0.4)

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
                             ({"channel": None}, "channel"), ({"disk_notify": ["email"]}, "disk_notify"),
                             ({"disk_repeat_days": -1}, "disk_repeat_days"), ({"people": "alice"}, "people"),
                             ({"io_share": 1.5}, "io_share"), ({"io_minutes": 2}, "io_minutes"),
                             ({"io_mounts": {"/home": {"read_bps": 0, "write_bps": 1}}}, "io_mounts /home")):  # fmt: skip
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
        texts = [c.args[1] for c in self.slack.dm.call_args_list]
        self.assertEqual(len(texts), 2, "one DM to u, one to v")
        self.assertTrue(texts[0].startswith("2: - `7`"), texts[0])

    def test_batch_and_interactive_alike(self):
        batch = dict(self.job("7", NOW - 3600, warned=NOW - 600), interactive=False)
        self.assertEqual(self.run_act([batch]), ["7"])

    def test_told_once_per_person_and_every_job_of_theirs_counts_as_told(self):
        jobs = [self.job("7", None), self.job("8", None)]
        self.run_act(jobs, due=["u"], notify=["dm"], cancel_after_hours=0)
        self.assertEqual(self.slack.dm.call_count, 1, "one DM for both jobs")
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

    def test_the_summary_lists_the_top_by_gpu_hours_and_by_idle_hours_mentioning_no_one(self):
        """Owner, 2026-11-28: the weekly summary mentions no one; it names the top people
        by GPU-hours and by idle hours, as plain names."""
        rows = report.collect_week(self.Prom(), 0)
        self.assertEqual([r["user"] for r in rows], ["bob", "alice", "carol"])
        text, blocks = report.render_week(rows, 5, top=2, now=7 * 86400)
        self.assertIn("250 GPU시간 중 GPU 유휴 시간 130 (52%)", text)
        self.assertEqual(blocks[0]["text"]["text"], ":bar_chart: 주간 GPU 사용  01-01 ~ 01-08")
        body = rich.read([b for b in blocks if b["type"] not in ("header", "context")])
        top = body[body.index("GPU시간 상위") : body.index("GPU 유휴 시간 상위")]
        self.assertEqual(
            top.splitlines()[1:], ["- bob: 120 GPU시간 (유휴 75%)", "- alice: 100 GPU시간 (유휴 40%)"]
        )
        idle = body[body.index("GPU 유휴 시간 상위") :]
        self.assertEqual(
            idle.splitlines()[1:3],
            ["- bob: 90 GPU시간 (할당 시간의 75%)", "- alice: 40 GPU시간 (할당 시간의 40%)"],
        )
        self.assertNotIn("carol", idle, "no idle hours, not on the idle list")
        self.assertNotIn("<@", str(blocks) + text, "no mention")
        note = rich.read([b for b in blocks if b["type"] == "context"])
        self.assertIn("5% 이하", note, "the threshold the hours were counted at, not a fixed 0%")
        self.assertNotIn("멘션", note)
        self.assertEqual(report.util_at_most(0), "0%", "nothing is under 0%")
        self.assertEqual(report.util_at_most(5), "5% 이하")
        _, zero = report.render_week(rows, 0)
        self.assertNotIn("0% 이하", rich.read(zero))

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
        shown = rich.read(blocks)
        self.assertIn("What to do", shown)
        self.assertIn("- week 1: 90 idle of 120 GPU-hours", shown)
        self.assertIn("50 idle GPU-hours and 50%", rich.read(blocks[-1:]))


if __name__ == "__main__":
    unittest.main()
