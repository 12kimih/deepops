"""Tests for gpu-usage (clustertools.gpu_usage) against Slurm 26.05 accounting fixtures."""

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from common import HERE, run_tool
from clustertools import gpu_usage, render, slurm
from clustertools.prometheus import Error as PrometheusError

ACCOUNTING = json.loads((HERE / "fixtures/slurm-26.05/accounting.json").read_text())
ANSI = re.compile(r"\033\[[0-9;]*m")


def gpu_job(task=False):
    """A finished GPU job of the capture (an array task when TASK)."""
    return copy.deepcopy(
        next(j for j in ACCOUNTING["jobs"] if gpu_usage.holds_gpus(j) and bool(j["array"]["job_id"]) == task)
    )


def with_slurm_gpu_figures(job, peak=87, mean=60, memory=6 * 2**30):
    """JOB as Slurm records it with AutoDetect=nvml: gres/gpuutil and gres/gpumem in the
    batch step's TRESUsageIn (the capture predates that)."""
    step = next(s for s in job["steps"] if s["step"]["name"] == "batch")
    usage = step["tres"]["requested"]
    usage["max"] += [{"type": "gres", "name": "gpuutil", "id": 1001, "count": peak},
                     {"type": "gres", "name": "gpumem", "id": 1002, "count": memory}]  # fmt: skip
    usage["average"] += [{"type": "gres", "name": "gpuutil", "id": 1001, "count": mean},
                         {"type": "gres", "name": "gpumem", "id": 1002, "count": memory // 2}]  # fmt: skip
    return job


class FakePrometheus:
    """Answers the four range queries per GPU; records what it was asked."""

    def __init__(self, gpus=None, error=None):
        self.gpus, self.error, self.asked = gpus or {}, error, []

    def query(self, expr, at=None):
        self.asked.append((expr, at))
        if self.error:
            raise PrometheusError(self.error)
        fn, metric = re.search(r"(\w+)_over_time\((job_device:\w+:max)", expr).groups()
        key = ("util" if "util" in metric else "memory") + ("_mean" if fn == "avg" else "_max")
        return [
            {"metric": {"host": host, "device": device}, "value": [at, str(figures[key])]}
            for (host, device), figures in self.gpus.items()
        ]


GPU = {
    ("gpu1", "nvidia3"): {"util_mean": 0.5, "util_max": 1.0, "memory_mean": 2**30, "memory_max": 2 * 2**30}
}


class ReportTests(unittest.TestCase):
    def test_slurm_figures_are_preferred_and_prometheus_is_not_asked(self):
        api = FakePrometheus(GPU)
        r = gpu_usage.report(with_slurm_gpu_figures(gpu_job()), "auto", api, now=0)
        self.assertEqual(api.asked, [])
        (row,) = r["rows"]
        self.assertEqual(row["source"], "slurm")
        self.assertTrue(row["where"].endswith(".batch"))
        self.assertEqual((row["util_mean"], row["util_max"]), (0.60, 0.87))
        self.assertEqual(row["memory_max_bytes"], 6 * 2**30)

    def test_without_slurm_figures_prometheus_answers_per_gpu_over_the_run(self):
        job = gpu_job()
        api = FakePrometheus(GPU)
        r = gpu_usage.report(job, "auto", api, now=0)
        self.assertEqual([row["source"] for row in r["rows"]], ["prometheus"])
        self.assertEqual(r["rows"][0]["where"], "gpu1/nvidia3")
        self.assertEqual(r["rows"][0]["util_max"], 1.0)
        self.assertIn("AutoDetect=nvml", r["notes"][0])
        # Evaluated at the job's end over exactly its run, selected by its Slurm name.
        span = job["time"]["end"] - job["time"]["start"]
        for expr, at in api.asked:
            self.assertEqual(at, job["time"]["end"])
            self.assertIn(f'{{job_id="{job["job_id"]}"}}[{max(span, gpu_usage.STEP)}s]', expr)
            self.assertTrue(expr.startswith("max by (host, device) ("))

    def test_an_array_task_is_selected_by_its_array_name(self):
        job = gpu_job(task=True)
        api = FakePrometheus(GPU)
        gpu_usage.report(job, "prometheus", api, now=0)
        name = f"{job['array']['job_id']}_{job['array']['task_id']['number']}"
        self.assertIn(f'job_id="{name}"', api.asked[0][0])

    def test_a_running_job_reads_up_to_now(self):
        job = gpu_job()
        job["time"]["end"] = 0
        api = FakePrometheus(GPU)
        gpu_usage.report(job, "prometheus", api, now=job["time"]["start"] + 30)
        self.assertEqual(api.asked[0][1], job["time"]["start"] + 30)
        self.assertIn(f"[{gpu_usage.STEP}s]", api.asked[0][0], "never a window under one sample")

    def test_each_source_can_be_asked_for_alone(self):
        job = with_slurm_gpu_figures(gpu_job())
        self.assertEqual([r["source"] for r in gpu_usage.report(job, "prometheus", FakePrometheus(GPU), 0)["rows"]],
                         ["prometheus"])  # fmt: skip
        self.assertEqual([r["source"] for r in gpu_usage.report(gpu_job(), "slurm", FakePrometheus(GPU), 0)["rows"]],
                         [])  # fmt: skip

    def test_what_is_missing_is_said_not_shown_as_idle(self):
        job = gpu_job()
        self.assertIn("no prometheus_url", " ".join(gpu_usage.report(job, "auto", None, 0)["notes"]))
        down = gpu_usage.report(job, "auto", FakePrometheus(error="http://m:9090: refused"), 0)
        self.assertEqual(down["rows"], [])
        self.assertIn("refused", down["notes"][-1])
        empty = gpu_usage.report(job, "auto", FakePrometheus({}), 0)
        self.assertIn("no samples", empty["notes"][-1])
        job["time"]["start"] = 0
        self.assertIn("not started", gpu_usage.report(job, "prometheus", FakePrometheus(GPU), 0)["notes"][-1])


class CpuTests(unittest.TestCase):
    def test_the_share_of_its_cpus_a_job_kept_busy_is_seffs(self):
        job = gpu_job()
        job["time"].update(elapsed=200, total={"seconds": 99, "microseconds": 1_000_000})
        cpus = slurm.tres_list(job["tres"]["allocated"])["cpu"]
        r = gpu_usage.report(job, "slurm", None, 0)
        self.assertEqual((r["cpus"], r["cpu_seconds"]), (cpus, 100.0))
        self.assertAlmostEqual(r["cpu_efficiency"], 100 / (cpus * 200))
        self.assertIn(f"CPU: 0.5 of {cpus} busy on average", gpu_usage.cpu_line(r))

    def test_a_running_jobs_share_says_it_counts_finished_steps(self):
        job = gpu_job()
        job["time"].update(elapsed=200, total={"seconds": 100})
        job["state"]["current"] = ["RUNNING"]
        self.assertTrue(gpu_usage.cpu_line(gpu_usage.report(job, "slurm", None, 0)).endswith(
            ", finished steps only"))  # fmt: skip

    def test_no_run_time_is_said_not_shown_as_idle(self):
        job = gpu_job()
        job["time"]["elapsed"] = 0
        r = gpu_usage.report(job, "slurm", None, 0)
        self.assertIsNone(r["cpu_efficiency"])
        self.assertIn("no run time", gpu_usage.cpu_line(r))


class RenderTests(unittest.TestCase):
    def test_rows_and_notes(self):
        theme = render.Theme(color=False, cols=100)
        r = gpu_usage.report(gpu_job(), "auto", FakePrometheus(GPU), 0)
        text = gpu_usage.render([r], theme)
        self.assertIn("gpu1/nvidia3", text)
        self.assertRegex(text, r"50%\s+100%\s+1\.0G\s+2\.0G\s+prometheus")
        self.assertIn("AutoDetect=nvml", text)
        self.assertIn(f"  {gpu_usage.cpu_line(r)}\n", text)


class CommandTests(unittest.TestCase):
    def run_on(self, jobs, *args):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "accounting.json"
            path.write_text(json.dumps({**ACCOUNTING, "jobs": jobs}))
            return run_tool("gpu_usage", *args, env={"ACCOUNTING": str(path)})

    def test_json_names_each_rows_source(self):
        done = self.run_on([with_slurm_gpu_figures(gpu_job())], "123", "--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        (report,) = json.loads(done.stdout)
        self.assertEqual([r["source"] for r in report["rows"]], ["slurm"])

    def test_a_job_is_found_in_every_cluster_the_accounting_holds(self):
        # A rename keeps the old cluster's history under its name: the same id may name
        # a job there and one here, and each report says which cluster it is from.
        old, new = gpu_job(), gpu_job()
        old["cluster"], new["cluster"] = "oldname", "newname"
        done = self.run_on([old, new], "123")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"job {old['job_id']} on oldname", done.stdout)
        self.assertIn(f"job {new['job_id']} on newname", done.stdout)

    def test_a_job_without_gpus_or_unknown_is_one_line(self):
        cpu = gpu_job()
        cpu["tres"]["allocated"] = [t for t in cpu["tres"]["allocated"] if t["type"] != "gres"]
        done = self.run_on([cpu], "123")
        self.assertEqual((done.returncode, done.stderr.strip()), (1, "gpu-usage: job 123 holds no GPUs"))
        done = self.run_on([], "123")
        self.assertEqual(
            (done.returncode, done.stderr.strip()), (1, "gpu-usage: no job 123 in Slurm's accounting")
        )

    def test_help_and_completion_come_from_the_definition(self):
        done = run_tool("gpu_usage", "--help")
        self.assertIn("Usage: gpu-usage [OPTIONS] JOB", done.stdout)
        self.assertIn("TRESUsageInAve", done.stdout)
        completion = run_tool("gpu_usage", "--completion", "bash").stdout
        self.assertIn("auto slurm prometheus", completion)
        self.assertNotIn(" job ", completion)


if __name__ == "__main__":
    unittest.main()
