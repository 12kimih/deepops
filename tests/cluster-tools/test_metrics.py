"""Tests for clustertools.metrics: GPU allocation as node_exporter textfile metrics."""

import re
import tempfile
import unittest
from pathlib import Path

from common import load, run_tool
from clustertools import metrics


def value(n):
    return {"set": True, "infinite": False, "number": n}


def running(job_id, user, nodes, gres_detail, state="RUNNING", **extra):
    return {"job_id": job_id, "user_name": user, "account": "acct1", "partition": "a10-1", "job_state": [state],
            "nodes": nodes, "gres_detail": gres_detail, "array_job_id": value(0),
            "array_task_id": {"set": False}, "array_task_string": "", **extra}  # fmt: skip


class TextfileTests(unittest.TestCase):
    def test_one_series_per_allocated_gpu_and_per_node_type(self):
        jobs = {"jobs": [
            running(1, "alice", "gpu1", ["gpu:a10:2(IDX:0-1)"]),
            running(2, "bob", "gpu[2-3]", ["gpu:a30:1(IDX:4)", "gpu:a30:1(IDX:0)"], array_job_id=value(9),
                    array_task_id=value(3)),
            running(3, "carol", "gpu4", ["gpu:a10:1(IDX:0)"], state="COMPLETED"),
            *load("jobs")["jobs"],  # pending: they hold nothing
        ]}  # fmt: skip
        text = metrics.textfile(jobs, load("nodes"))
        allocated = [line for line in text.splitlines() if line.startswith("slurm_gpu_allocated{")]
        self.assertEqual(len(allocated), 4)
        self.assertIn('slurm_gpu_allocated{node="gpu1",gpu="1",gpu_type="a10",user="alice",account="acct1",'
                      'partition="a10-1",job_id="1"} 1', text)  # fmt: skip
        self.assertIn('node="gpu3",gpu="0",gpu_type="a30",user="bob"', text)
        self.assertIn('job_id="9_3"', text)
        self.assertNotIn("carol", text)
        self.assertIn('slurm_node_gpus{node="gpu4",gpu_type="a30"} 7', text)
        self.assertIn("# TYPE slurm_gpu_allocated gauge", text)

    def test_no_label_the_scrape_attaches_itself(self):
        # A scraped label named like a target label is renamed exported_*.
        text = metrics.textfile({"jobs": [running(1, "u", "gpu1", ["gpu:a10:1(IDX:0)"])]}, load("nodes"))
        names = set(re.findall(r'[{,]([a-z_]+)="', text))
        self.assertTrue(names)
        self.assertEqual(names & {"job", "instance", "host"}, set())

    def test_label_values_are_escaped(self):
        text = metrics.textfile({"jobs": [running(1, 'a"b\\c', "gpu1", ["gpu:a10:1(IDX:0)"])]}, {"nodes": []})
        self.assertIn('user="a\\"b\\\\c"', text)

    def test_each_gpu_carries_its_own_type_and_joins_its_node(self):
        jobs = {"jobs": [running(1, "u", "gpu[1-2]", ["gpu:a10:1(IDX:0)", "gpu:a30:1(IDX:3)"]),
                         running(2, "u", "gpu3", ["gpu:2(IDX:0-1)"])]}  # fmt: skip
        nodes = {"nodes": [{"name": "gpu3", "gres": "gpu:2"}]}
        text = metrics.textfile(jobs, nodes)
        self.assertIn('node="gpu1",gpu="0",gpu_type="a10"', text)
        self.assertIn('node="gpu2",gpu="3",gpu_type="a30"', text)
        # Untyped: the same empty type on the allocation and the node, so the two join.
        self.assertIn('node="gpu3",gpu="1",gpu_type=""', text)
        self.assertIn('slurm_node_gpus{node="gpu3",gpu_type=""} 2', text)


class WriteTests(unittest.TestCase):
    def test_command_writes_and_keeps_the_old_file_on_failure(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "slurm_gpu.prom"
            r = run_tool("metrics", str(path))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("slurm_node_gpus{", path.read_text())
            path.write_text("previous\n")
            r = run_tool("metrics", str(path), env={"FAKE_FAIL": "slurm_load_jobs error: down"})
            self.assertEqual(r.returncode, 1)
            self.assertEqual(path.read_text(), "previous\n")
            self.assertEqual(run_tool("metrics").returncode, 2)

    def test_a_file_error_is_not_blamed_on_slurm(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "no-dir" / "slurm_gpu.prom")
            r = run_tool("metrics", path)
            self.assertEqual(
                (r.returncode, r.stderr.strip()), (1, f"metrics: {path}: No such file or directory")
            )


if __name__ == "__main__":
    unittest.main()
