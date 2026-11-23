"""cluster_tools.gpu_diag_idle: which nodes a pass takes, how a run is judged, and what
each outcome does to the node. Slurm and dcgmi are stand-ins; nothing is run."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
from cluster_tools import gpu_diag_idle as gd  # noqa: E402
from cluster_tools import slurm  # noqa: E402

NOW = 1_800_000_000
CFG = {"level": 2, "timeouts": {"1": 600, "2": 1200}, "period_days": 6, "budget_seconds": 36000,
       "drain_on_failure": True, "reservation_prefix": "gpu-diag"}  # fmt: skip


def node(name, state=("IDLE",), gres="gpu:a10:8(S:0-1)", reservation=""):
    return {"name": name, "state": list(state), "gres": gres, "reservation": reservation}


# dcgmi diag -j as NVIDIA documents it, and as its source writes it.
def docs_shape(status):
    return json.dumps({"category": "Integration", "tests": [
        {"name": "pcie", "results": [{"entity_group": "GPU", "entity_id": 0, "status": "Pass"}]},
        {"name": "memory", "results": [{"entity_group": "GPU", "entity_id": 0, "status": status},
                                       {"entity_group": "GPU", "entity_id": 1, "status": "Pass"}]}]})  # fmt: skip


def source_shape(status):
    return json.dumps({"DCGM Diagnostic": {"version": "4.4", "test_categories": [
        {"category": "Hardware", "tests": [{"name": "diagnostic", "results": [{"entity_id": 0, "status": status}],
                                            "test_summary": {"status": status}}]}]}})  # fmt: skip


class Choosing(unittest.TestCase):
    def test_every_gpu_of_the_node_is_requested(self):
        self.assertEqual(gd.node_gres(node("a")), "gpu:a10:8")
        self.assertEqual(gd.node_gres(node("b", gres="gpu:b10:7(S:0-1)")), "gpu:b10:7")
        self.assertEqual(gd.node_gres(node("c", gres="")), "")

    def test_only_idle_unreserved_gpu_nodes_not_diagnosed_lately_oldest_first(self):
        nodes = [
            node("busy", ("MIXED",)), node("drained", ("IDLE", "DRAIN")), node("maint", ("IDLE", "MAINTENANCE")),
            node("reserved", reservation="window"), node("cpu", gres=""), node("fresh"), node("old"), node("never"),
        ]  # fmt: skip
        state = {"fresh": {"diagnosed": NOW - 2 * gd.DAY}, "old": {"diagnosed": NOW - 9 * gd.DAY}}
        self.assertEqual([n["name"] for n in gd.candidates(nodes, state, CFG, NOW)], ["never", "old"])


class Verdict(unittest.TestCase):
    def test_a_failed_test_is_the_gpus_verdict_in_either_shape(self):
        self.assertEqual(gd.verdict(226, docs_shape("Fail")), ("fail", ["memory"], ""))
        self.assertEqual(gd.verdict(226, source_shape("Fail")), ("fail", ["diagnostic"], ""))

    def test_a_clean_pass(self):
        self.assertEqual(gd.verdict(0, docs_shape("Pass")), ("pass", [], ""))
        self.assertEqual(gd.verdict(0, "progress text\n" + source_shape("Pass")), ("pass", [], ""))
        self.assertEqual(gd.verdict(0, docs_shape("Skip"))[0], "pass")

    def test_anything_else_is_the_tools_problem(self):
        unreachable = json.dumps(
            {"DCGM Diagnostic": {"version": "4.4", "runtime_error": "Unable to connect"}}
        )
        for rc, out, why in (
            (235, unreachable, "Unable to connect"), (1, "Error: no JSON", "unreadable"), (0, "{}", "no test results"),
            (226, docs_shape("Pass"), "without a failed test"), (0, "{not json", "unreadable"),
        ):  # fmt: skip
            with self.subTest(out=out[:30]):
                result, failed, reason = gd.verdict(rc, out)
                self.assertEqual((result, failed), ("error", []))
                self.assertIn(why, reason)


class Node(unittest.TestCase):
    """diagnose(): the Slurm calls each outcome makes."""

    def run_node(self, outputs, hold_fails=False, cfg=CFG):
        calls, runs = [], iter(outputs)

        def fake_slurm(*cmd):
            calls.append(cmd[:3])
            if hold_fails and cmd[1] == "create":
                raise slurm.Failed("scontrol: error: Requested nodes are busy")
            return ""

        def fake_step(*cmd, timeout):
            calls.append((cmd[0],))
            rc, out = next(runs)
            return rc, out, ""

        with mock.patch.object(gd.slurm, "run", fake_slurm), mock.patch.object(gd.slurm, "step", fake_step):
            rec = gd.diagnose(node("n1"), cfg, log=lambda *a, **k: None)
        return rec, calls

    def test_failed_twice_drains_with_its_own_reason(self):
        rec, calls = self.run_node([(226, docs_shape("Fail"))] * 2)
        self.assertEqual((rec["result"], rec["failed"], rec["drained"]), ("fail", ["memory"], True))
        self.assertEqual(calls.count(("srun",)), 2, "confirmed by one rerun")
        self.assertIn(("scontrol", "delete", "ReservationName=gpu-diag-n1"), calls)
        self.assertEqual(
            calls[-1], ("scontrol", "update", "nodename=n1"), "drained after the reservation goes"
        )

    def test_failed_then_passed_is_flaky_and_stays_in_service(self):
        rec, calls = self.run_node([(226, docs_shape("Fail")), (0, docs_shape("Pass"))])
        self.assertEqual((rec["result"], rec["failed"], rec["drained"]), ("flaky", ["memory"], False))
        self.assertNotIn(("scontrol", "update", "nodename=n1"), calls)

    def test_a_tool_error_never_drains(self):
        rec, calls = self.run_node([(235, json.dumps({"runtime_error": "Unable to connect"}))])
        self.assertEqual((rec["result"], rec["drained"]), ("error", False))
        self.assertEqual(calls.count(("srun",)), 1)
        self.assertNotIn(("scontrol", "update", "nodename=n1"), calls)
        self.assertIn(("scontrol", "delete", "ReservationName=gpu-diag-n1"), calls)

    def test_a_failure_whose_rerun_cannot_run_is_an_error_not_a_drain(self):
        rec, calls = self.run_node([(226, docs_shape("Fail")), (1, "Error")])
        self.assertEqual((rec["result"], rec["drained"]), ("error", False))
        self.assertIn("failed (memory)", rec["why"])

    def test_a_node_slurm_will_not_reserve_is_skipped_untouched(self):
        rec, calls = self.run_node([], hold_fails=True)
        self.assertEqual(rec["result"], "skipped")
        self.assertNotIn(("srun",), calls)
        self.assertNotIn(("scontrol", "delete", "ReservationName=gpu-diag-n1"), calls)

    def test_without_drain_on_failure_a_confirmed_failure_only_reports(self):
        rec, calls = self.run_node([(226, docs_shape("Fail"))] * 2, cfg={**CFG, "drain_on_failure": False})
        self.assertEqual((rec["result"], rec["drained"]), ("fail", False))
        self.assertNotIn(("scontrol", "update", "nodename=n1"), calls)

    def test_the_hold_and_the_job(self):
        hold = gd.reservation_args("gpu-diag-n1", "n1", 45)
        self.assertEqual(hold[:2], ["create", "reservation"])
        self.assertIn("Flags=MAINT", hold)
        self.assertFalse([a for a in hold if "IGNORE_JOBS" in a], "a job that landed must make it fail")
        job = gd.diag_args("n1", "gpu-diag-n1", "gpu:b10:7", 2, 1200)
        for arg in (
            "--reservation=gpu-diag-n1",
            "--nodelist=n1",
            "--exclusive",
            "--gres=gpu:b10:7",
            "--time=20",
        ):
            self.assertIn(arg, job)
        self.assertEqual(job[-5:], ["dcgmi", "diag", "-r", "2", "-j"])


class Pass(unittest.TestCase):
    def test_a_pass_records_each_node_and_publishes_its_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = {**CFG, "textfile_dir": f"{d}/prom", "textfile": "gpu-diag.prom", "metrics_file": f"{d}/keep/gpu-diag.prom",
                   "state_file": f"{d}/state.json", "raw_dir": f"{d}/raw"}  # fmt: skip
            nodes = {"nodes": [node("good"), node("bad"), node("busy", ("MIXED",))]}
            outcome = {"good": [(0, docs_shape("Pass"))], "bad": [(226, docs_shape("Fail"))] * 2}

            def fake_step(*cmd, timeout):
                rc, out = outcome[cmd[2].split("=")[1]].pop(0)
                return rc, out, ""

            with (
                mock.patch.object(gd.slurm, "load", lambda kind: nodes),
                mock.patch.object(gd.slurm, "run", lambda *c: ""),
                mock.patch.object(gd.slurm, "step", fake_step),
            ):
                gd.run_pass(cfg, now=NOW, log=lambda *a, **k: None)
            text = Path(f"{d}/prom/gpu-diag.prom").read_text()
            self.assertEqual(text, Path(cfg["metrics_file"]).read_text())
            self.assertIn(
                'gpu_diag_idle_result{node="bad",result="fail",tests="memory",drained="yes"} 1', text
            )
            self.assertIn('gpu_diag_idle_result{node="good",result="pass",tests="",drained="no"} 1', text)
            self.assertNotIn('node="busy"', text)
            self.assertIn(f"gpu_diag_idle_last_success_timestamp_seconds{{}} {NOW}", text)
            self.assertIn(f"gpu_diag_idle_since_seconds{{}} {NOW}", text, "overdue counts from here")
            state = json.loads(Path(cfg["state_file"]).read_text())
            self.assertGreater(state["nodes"]["good"]["diagnosed"], 0)
            self.assertTrue(os.path.exists(f"{d}/raw/bad.json"), "the raw output is kept")

    def test_the_level_needs_its_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "c.json")
            base = {**CFG, "textfile_dir": d, "textfile": "x.prom", "metrics_file": f"{d}/m", "state_file": f"{d}/s",
                    "raw_dir": f"{d}/r"}  # fmt: skip
            path.write_text(json.dumps(base))
            gd.load_config(path)
            path.write_text(json.dumps({**base, "level": 3}))
            with self.assertRaises(gd.config.ConfigError):
                gd.load_config(path)


if __name__ == "__main__":
    unittest.main()
