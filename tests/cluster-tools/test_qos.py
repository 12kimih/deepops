"""Tests for qos (clustertools.qos) against Slurm 25.11 fixtures."""

import json
import re
import unittest

from common import load, run_tool, running
from clustertools import qos
from clustertools import render

ANSI = re.compile(r"\033\[[0-9;]*m")


def model(who="user14", jobs=None, **kw):
    data = load("jobs")
    data["jobs"] += jobs or []
    return qos.build(load("qos"), load("assoc"), data, who, **kw)


class BuildTests(unittest.TestCase):
    def test_mine_is_the_union_over_every_account(self):
        m = model("user14")
        self.assertEqual(sorted(r["name"] for r in m["qos"] if r["mine"]), ["high_t2", "hotfix", "low"])
        self.assertEqual(m["default_qos"], "low")

    def test_qos_nobody_holds_is_hidden_unless_all(self):
        self.assertNotIn("normal", [r["name"] for r in model()["qos"]])
        self.assertEqual(model()["hidden"], 1)
        self.assertIn("normal", [r["name"] for r in model(show_all=True)["qos"]])

    def test_mine_only(self):
        self.assertEqual(sorted(r["name"] for r in model("user1", mine_only=True)["qos"]), ["high_t1", "low"])

    def test_preempted_by_is_the_reverse_of_preempts(self):
        rows = {r["name"]: r for r in model()["qos"]}
        self.assertEqual(rows["high_t2"]["preempts"], ["low"])
        self.assertIn("hotfix", rows["high_t2"]["preempted_by"])
        self.assertEqual(rows["hotfix"]["preempted_by"], [])

    def test_usage_counts_running_jobs_of_that_qos_only(self):
        jobs = [
            running(1, "high_t2", "cpu=8,gres/gpu=4,gres/gpu:a10=4"),
            running(2, "high_t2", "gres/gpu=1,gres/gpu:a30=1"),
            dict(running(3, "high_t2", "gres/gpu:a10=9"), job_state=["COMPLETED"]),
        ]
        gpu = {r["name"]: r for r in model(jobs=jobs)["qos"]}["high_t2"]["gpu"]
        self.assertEqual((gpu["a10"]["used"], gpu["a30"]["used"]), (4, 1))
        self.assertEqual(gpu["a10"]["limit"], 14)

    def test_no_usage(self):
        m = model(usage=False)
        self.assertTrue(all(q["used"] is None for r in m["qos"] for q in r["gpu"].values()))

    def test_wall_and_types(self):
        rows = {r["name"]: r for r in model()["qos"]}
        self.assertEqual(rows["low"]["max_wall_seconds"], 86400)
        self.assertEqual(qos.clock(86400), "1-00:00:00")
        self.assertEqual(model()["gpu_types"], ["a10", "a30"])


class ShowTests(unittest.TestCase):
    def lines(self, cols, m=None):
        out = qos.show(render.Theme(color=True, cols=cols), m or model())
        return [ANSI.sub("", x) for x in out.splitlines()]

    def test_fits_the_window(self):
        for cols in (70, 90, 120, 200):
            lines = self.lines(cols)
            table = lines[: lines.index("")]  # heading, header, rule and rows
            self.assertGreater(len(table), 5)
            for line in table:
                self.assertLessEqual(render.width(line), cols, (cols, line))

    def test_bars_return_when_there_is_room(self):
        self.assertTrue(any("░" in x for x in self.lines(200)))
        self.assertFalse(any("░" in x for x in self.lines(70)))

    def test_yours_marked_and_data_lines_only(self):
        lines = self.lines(120)
        text = "\n".join(lines)
        self.assertRegex(text, r"(?m)^▌ hotfix ")
        self.assertIn("user14 submits as low", text)
        self.assertIn("1 hidden (--all)", lines[0])  # a figure for the heading, not a sentence
        # What the table means is --help's; below the table only facts about this data.
        self.assertNotRegex(text, "MAX WALL means|Usage is every|QOSGrpGRES is waiting|limit of 0")

    def test_nobody(self):
        m = model("nobody")
        m["qos"] = []
        self.assertIn("nobody may not submit", qos.show(render.Theme(color=False), m))


class CommandTests(unittest.TestCase):
    def test_runs_against_fake_slurm(self):
        r = run_tool("qos", "--color", "always")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("QoS", r.stdout)
        self.assertIsNone(re.search("[–—·…]| -- ", r.stdout))

    def test_json_is_the_model(self):
        r = run_tool("qos", "--json", env={"QOS_OPTIONS": "--mine"})
        data = json.loads(r.stdout)
        self.assertEqual(data["user"], "user1")
        self.assertGreater(len(data["qos"]), 2, "--json must ignore $QOS_OPTIONS")

    def test_help_explains_the_table(self):
        self.assertIn("A limit of 0 is not a full quota", run_tool("qos", "--help").stdout)

    def test_help_levels(self):
        self.assertIn("More: qos --help", run_tool("qos", "-h").stdout)
        self.assertIn("GrpTRES", run_tool("qos", "--help").stdout)

    def test_unknown_usage_is_not_zero(self):
        out = run_tool("qos", "--no-usage").stdout
        self.assertRegex(out, r" -/14 ")
        self.assertNotIn("Usage is every RUNNING job", out)

    def test_failed_usage_read_is_reported_not_zero(self):
        r = run_tool("qos", env={"FAKE_FAIL": "slurm_load_jobs error: Rate limit exceeded"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("live usage unavailable: slurm_load_jobs error: Rate limit exceeded", r.stdout)
        self.assertRegex(r.stdout, r" -/14 ")

    def test_stalled_usage_read_is_bounded(self):
        r = run_tool("qos", env={"FAKE_SLEEP": "3", "CLUSTER_TOOLS_TIMEOUT": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("live usage unavailable: no answer in 1 s (slurmctld slow, or rate limiting", r.stdout)

    def test_stalled_accounting_read_fails_in_one_line(self):
        r = run_tool("qos", env={"FAKE_DBD_SLEEP": "3", "CLUSTER_TOOLS_TIMEOUT": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertEqual(
            r.stderr.strip().splitlines(),
            ["qos: sacctmgr: no answer in 1 s (slurmctld slow, or rate limiting your requests)"],
        )

    def test_exit_codes(self):
        self.assertEqual(run_tool("qos", "--bogus").returncode, 2)
        missing = run_tool("qos", path=False)
        self.assertEqual(missing.returncode, 127)
        self.assertIn("sacctmgr not found", missing.stderr)


if __name__ == "__main__":
    unittest.main()
