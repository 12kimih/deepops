"""cluster_tools.rules: every enforcer has its rule, and each rule's overview agrees with
its enforcer on the same figures (the owner's self-service rules, 2026-11-27)."""

import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "roles/cluster_tools_lib/files"
sys.path.insert(0, str(LIB))
from cluster_tools import disk_report, io_report, rules, scratch_space, submit_holds, usage_report  # noqa: E402

# What acts on a person: cancels a job, sends a notice, purges files, holds submissions.
ACTS = re.compile(r"\bscancel\b|\.dm\(|def dm_text|def purge\(|def holds\(")
# Modules that act so and are not a rule a person is measured against, and why.
NOT_A_RULE = {
    "slack": "the Slack client every notice is sent through",
    "job_notice": "tells a job's owner the job ended (--mail-type): no limit",
}
NOW = 1_800_000_000


class FakeProm:
    """Instant queries over a node_exporter textfile: NAME or NAME{label="value"}."""

    def __init__(self, text):
        from cluster_tools import prom

        self.values = prom.parse(text)

    def query(self, expr, at=None):
        name, _, want = expr.partition("{")
        wanted = dict(re.findall(r'(\w+)="([^"]*)"', want))
        return [{"metric": dict(labels), "value": [0, value]} for (n, labels), value in self.values.items()
                if n == name and wanted.items() <= dict(labels).items()]  # fmt: skip


class Registry(unittest.TestCase):
    def test_every_enforcer_has_a_rule(self):
        acting = {p.stem for p in (LIB / "cluster_tools").glob("*.py") if ACTS.search(p.read_text())}
        covered = {r.enforcer for r in rules.RULES}
        self.assertEqual(
            sorted(acting - covered - set(NOT_A_RULE)), [], "add the rule to cluster_tools.rules"
        )
        self.assertEqual(sorted(set(NOT_A_RULE) - acting), [], "stale NOT_A_RULE entries")

    def test_each_rule_names_a_usage_subcommand_and_its_settings(self):
        from cluster_tools import usage

        for r in rules.RULES:
            with self.subTest(rule=r.id):
                words = r.check.split()
                self.assertEqual(words[0], "usage")
                self.assertTrue(len(words) == 1 or words[1] in usage.GROUP.subs, r.check)
                self.assertTrue(r.settings and r.title)

    def test_the_json_keys_are_pinned(self):
        doc = rules.document("usage", "kim", [rules.row("disk", "a", "b", "ok")], NOW, extra=1)
        self.assertEqual(list(doc)[: len(rules.JSON_KEYS)], list(rules.JSON_KEYS))
        self.assertEqual(tuple(doc["rules"][0]), rules.ROW_KEYS)
        self.assertEqual(doc["schema"], 1)


class Agreement(unittest.TestCase):
    """Each measure against its enforcer's own decision on the same figures."""

    def test_gpu_idle_is_the_usage_reports_idle(self):
        cfg = {"gpu_idle": {"idle_hours": 2, "idle_util_threshold": 0, "cancel_after_hours": 0}}
        job = {
            "id": "7",
            "label": "7",
            "user": "kim",
            "running": True,
            "runtime": 3 * 3600,
            "gpus": [("g1", 0)],
        }
        for util, idle in ((0.0, True), (50.0, False)):
            gpu = {"util_max": {("g1", 0): util}, "util": {("g1", 0): util}, "mem_gib": {}}
            with self.subTest(util=util), mock.patch.object(usage_report, "parse_jobs", return_value=([dict(job)], {})), \
                    mock.patch.object(usage_report, "collect_gpus", return_value=gpu):  # fmt: skip
                found = usage_report.analyze([dict(job)], gpu, 7200, 0)
                state = rules.gpu_idle("kim", cfg, {"jobs": []}, object(), NOW).state
                self.assertEqual(bool(found), idle)
                self.assertEqual(state, "warn" if idle else "ok")
        cfg["gpu_idle"]["cancel_after_hours"] = 4
        gpu = {"util_max": {("g1", 0): 0.0}, "util": {}, "mem_gib": {}}
        with mock.patch.object(usage_report, "parse_jobs", return_value=([dict(job)], {})), \
                mock.patch.object(usage_report, "collect_gpus", return_value=gpu):  # fmt: skip
            self.assertEqual(
                rules.gpu_idle("kim", cfg, {"jobs": []}, object(), NOW).state, "act", "a cancel follows"
            )

    def test_disk_is_the_disk_reports_levels_and_the_hold(self):
        entry = {"used": 5, "hard": 5, "warn": 4, "inodes": 1, "inode_hard": 0, "inode_warn": 0}
        data = {"users": {("nfs1", "/home", "kim"): entry}}
        with mock.patch.object(disk_report, "collect", return_value=data):
            self.assertEqual(disk_report.past(entry)[0][3], "hard")
            row = rules.disk("kim", {"disk": {"holds_on": True, "holds_from": 0}}, object(), NOW)
            self.assertEqual((row.state, row.when), ("act", "새 잡 제출 보류 (줄일 때까지)"))
            entry["used"] = 4.5
            self.assertEqual(disk_report.past(entry)[0][3], "warn")
            self.assertEqual(rules.disk("kim", {"disk": {"holds_on": True}}, object(), NOW).state, "warn")
            entry["used"] = 1
            self.assertEqual(disk_report.past(entry), [])
            self.assertEqual(rules.disk("kim", {"disk": {"holds_on": True}}, object(), NOW).state, "ok")

    def test_scratch_is_the_purge_runs_own_figures(self):
        """What the purge run published for each person (scratch_space.metrics, from the
        plan it writes their list from): every caller reads it, the bot too (the list
        is in the person's own directory: Permission denied, 2026-11-27)."""
        cfg = {"scratch": {"root": "/s", "stale_days": 60, "grace_days": 14}}
        due = {"/s/kim/a": {"user": "kim", "due": NOW + 9 * 86400, "bytes": 100},
               "/s/kim/b": {"user": "kim", "due": NOW + 2 * 86400, "bytes": 50},
               "/s/lee/c": {"user": "lee", "due": NOW + 86400, "bytes": 7}}  # fmt: skip
        text = scratch_space.metrics({"root": "/s", "stale_days": 60}, due, {}, (0, 0), 3, False, NOW, None)
        prom = FakeProm(text)
        row = rules.scratch("kim", cfg, prom, NOW)
        first = time.strftime("%Y-%m-%d", time.localtime(NOW + 2 * 86400))
        self.assertEqual((row.state, row.detail["first"]), ("act", first))
        self.assertIn("2개", row.mine)
        self.assertEqual(rules.scratch("park", cfg, prom, NOW).state, "ok", "nothing of theirs due")
        self.assertEqual(rules.scratch("kim", cfg, prom, NOW + 3 * 86400).state, "?", "the run stopped")
        self.assertEqual(rules.scratch("kim", cfg, FakeProm(""), NOW).state, "?", "never ran")
        self.assertEqual(rules.scratch("kim", cfg, None, NOW).state, "?")

    def test_ssh_rate_is_the_login_nodes_own_counts(self):
        """The row reads what cluster_tools.ssh_rate publishes from the counts it decides
        by: the warning level it computed, the refusal it applies."""
        from cluster_tools import ssh_rate

        with tempfile.TemporaryDirectory() as d:
            cfg = {"mode": "refuse", "window_minutes": 10, "warn_sessions": 90, "refuse_sessions": 100,
                   "block_minutes": 10, "block_max_minutes": 60, "exempt_users": [], "state_dir": d,
                   "textfile": os.path.join(d, "out.prom"), "docs_url": ""}  # fmt: skip
            for login, n, blocked in (("kim", 95, False), ("lee", 101, True), ("park", 3, False)):
                state = {"sessions": [NOW - 60 + i * 0.1 for i in range(n)]}
                if blocked:
                    allowed, state = ssh_rate.decide(state, cfg, NOW)
                    self.assertFalse(allowed)
                Path(d, login).write_text(__import__("json").dumps(state))
            ssh_rate.metrics(cfg, NOW)
            prom = FakeProm(Path(cfg["textfile"]).read_text())
        on = {"ssh": {"on": True}}
        states = {who: rules.ssh(who, on, prom, NOW).state for who in ("kim", "lee", "park", "nobody")}
        self.assertEqual(states, {"kim": "warn", "lee": "act", "park": "ok", "nobody": "ok"})
        self.assertIn("새 접속 거부", rules.ssh("lee", on, prom, NOW).when)
        self.assertEqual(rules.ssh("kim", {}, prom, NOW).state, "off")
        self.assertEqual(rules.ssh("kim", on, FakeProm(""), NOW).state, "?", "no login node reports yet")

    def test_io_is_the_hot_nodes_jobs(self):
        hot = [{"host": "gpu1", "mount": "/home", "jobs": [("12", "kim", 1.0), ("13", "lee", 2.0)]}]
        cfg = {"io": {"share": 0.4, "minutes": 15, "mounts": {"/home": {}}}}
        with mock.patch.object(io_report, "collect", return_value=hot):
            self.assertEqual(rules.io("kim", cfg, object()).state, "warn")
            self.assertEqual(rules.io("park", cfg, object()).state, "ok")

    def test_login_reads_the_user_slice(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root, "user.slice", "user-1000.slice")
            base.mkdir(parents=True)
            for name, value in (("memory.current", "95"), ("memory.max", "100"), ("pids.current", "1"),
                                ("pids.max", "100"), ("cpu.max", "max 100000")):  # fmt: skip
                (base / name).write_text(value)
            row = rules.login(
                "kim", {"login": {"MemoryMax": "100"}}, 1000, cgroup_root=root, sleep=lambda s: None
            )
            self.assertEqual(row.state, "warn")
            (base / "memory.current").write_text("100")
            row = rules.login(
                "kim", {"login": {"MemoryMax": "100"}}, 1000, cgroup_root=root, sleep=lambda s: None
            )
            self.assertEqual(row.state, "act")

    def test_the_queue_is_slurms_max_submit_with_array_tasks(self):
        jobs = {"jobs": [{"user_name": "kim", "account": "a", "job_state": ["PENDING"], "array_task_string": "0-8"},
                         {"user_name": "kim", "account": "a", "job_state": ["RUNNING"]}]}  # fmt: skip
        cap = {"set": True, "infinite": False, "number": 10}
        assoc = {"associations": [{"user": "kim", "account": "a", "max": {"jobs": {"total": cap}}}]}
        self.assertEqual(rules.queued("kim", jobs, assoc).state, "act")
        cap["number"] = 100
        self.assertEqual(rules.queued("kim", jobs, assoc).state, "ok")

    def test_membership_is_the_submit_holds_end(self):
        with tempfile.TemporaryDirectory() as d:
            ends = {"ends": "2020-01-01", "login_expires": "2020-02-01"}
            Path(d, "kim").write_text(json.dumps(ends))
            self.assertTrue(submit_holds.ends_holds({"kim": ends}, NOW))
            self.assertEqual(rules.membership("kim", {"ends_dir": d}, NOW).state, "act")
            self.assertEqual(rules.membership("lee", {"ends_dir": d}, NOW).state, "ok")
            self.assertEqual(
                rules.membership("kim", {}, NOW, ends={"kim": ends}).state, "act", "the bot's copy"
            )


class Overview(unittest.TestCase):
    def test_a_source_down_is_unknown_never_ok_and_exits_3(self):
        from cluster_tools import slurm
        from cluster_tools.exits import AT_RISK, OK, UNKNOWN

        def load(kind):
            raise slurm.Error("slurmctld down")

        rows = rules.overview("kim", {"prometheus_url": "", "gpu_idle": {"idle_hours": 2, "idle_util_threshold": 0}},
                              uid=1000, prom=None, load=load, now=NOW, here=False)  # fmt: skip
        by = {r.rule: r.state for r in rows}
        self.assertEqual((by["gpu-idle"], by["queued-jobs"]), ("?", "?"))
        self.assertEqual(rules.exit_status(rows), UNKNOWN)
        self.assertEqual(rules.exit_status([rules.row("disk", "", "", "warn"), *rows]), AT_RISK)
        self.assertEqual(rules.exit_status([rules.row("disk", "", "", "ok")]), OK)
        self.assertEqual([r.rule for r in rows], [r.id for r in rules.RULES], "every rule, in order")

    def test_a_reader_who_is_not_the_person_gets_reasons_never_errors(self):
        """rules.overview as the bot reads it (another account, not on the login node):
        each source that fails is "?" with a short Korean reason, never an errno, a path
        or a URL; each that answers is measured."""
        from cluster_tools import prometheus, slurm

        class Down:
            def query(self, expr, at=None):
                raise prometheus.Error("http://prom.example.invalid:9090: connection refused")

        def load(kind):
            raise slurm.Error("slurm.example.invalid:6820: [Errno 111] Connection refused")

        with tempfile.TemporaryDirectory() as d:
            Path(d, "kim").write_text('{"ends": "2020-01-01", "login_expires": "2020-02-01"}')
            os.chmod(Path(d, "kim"), 0)
            if os.access(Path(d, "kim"), os.R_OK):
                self.skipTest("root reads every file")
            cfg = {"gpu_idle": {"idle_hours": 2, "idle_util_threshold": 0},
                   "disk": {"holds_on": False}, "scratch": {"root": d, "stale_days": 60, "grace_days": 14},
                   "io": {"share": 0.4, "minutes": 15, "mounts": {"/home": {}}}, "ssh": {"on": True}, "ends_dir": d}  # fmt: skip
            rows = rules.overview("kim", cfg, prom=Down(), load=load, now=NOW, here=False)
            for r in rows:
                with self.subTest(rule=r.rule):
                    self.assertEqual(r.state, "?")
                    said = f"{r.mine} {r.limit} {r.when}"
                    self.assertNotRegex(said, r"Errno|/|https?:|\.invalid", "a reason, not the error")
                    self.assertRegex(r.when, "[가-힣]")
            os.chmod(Path(d, "kim"), 0o644)
            jobs = {"jobs": [], "associations": []}
            login_node = (
                "ssh_rate_refuse_sessions 100\nssh_rate_warn_sessions 90\nssh_rate_window_seconds 600\n"
            )
            answers = FakeProm(
                scratch_space.metrics({"root": d, "stale_days": 60}, {}, {}, (0, 0), 0, False, NOW, None)
                + login_node
            )
            with mock.patch.object(io_report, "collect", return_value=[]):  # its rates: beyond FakeProm
                rows = rules.overview("kim", cfg, prom=answers, load=lambda kind: jobs, now=NOW, here=False)
            states = {r.rule: r.state for r in rows}
            self.assertEqual(states.pop("login-limits"), "?", "measured on the login node only")
            self.assertNotIn("?", states.values(), states)
            self.assertEqual(states["membership-end"], "act", "readable: read")


if __name__ == "__main__":
    unittest.main()
