"""cluster_tools.rules: every enforcer has its rule, and each rule's overview agrees with
its enforcer on the same figures (the owner's self-service rules, 2026-11-27)."""

import json
import os
import re
import sys
import tempfile
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

    def test_scratch_is_the_cleanups_list(self):
        with tempfile.TemporaryDirectory() as root:
            os.mkdir(os.path.join(root, "kim"))
            cfg = {"scratch": {"root": root, "stale_days": 60, "grace_days": 14}}
            self.assertEqual(rules.scratch("kim", cfg, NOW).state, "ok", "no list: nothing due")
            listed = "# header\n2026-12-10\t100\t/s/kim/a\n2026-12-01\t50\t/s/kim/b\n"
            Path(root, "kim", scratch_space.LIST_NAME).write_text(listed)
            _, entries = scratch_space.read_list(listed)
            row = rules.scratch("kim", cfg, NOW)
            self.assertEqual((row.state, row.detail["first"]), ("act", min(d for d, _, _ in entries)))
            self.assertIn(f"{len(entries)}개", row.mine)
            self.assertEqual(
                rules.scratch("kim", {"scratch": {**cfg["scratch"], "root": root + "/x"}}, NOW).state, "?"
            )

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
        from cluster_tools.cli import AT_RISK, OK, UNKNOWN

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


if __name__ == "__main__":
    unittest.main()
