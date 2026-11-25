"""Unit tests for cluster_tools.cpus (roles/cluster_tools_lib): the CPUs a process may use."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from cluster_tools import cpus  # noqa: E402


class Usable(unittest.TestCase):
    def tree(self, quotas):
        """A cgroup tree under a temporary root: {relative path: cpu.max text}; returns
        (root, the /proc/self/cgroup file naming the deepest one)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "cgroup"
        for rel, text in quotas.items():
            (root / rel).mkdir(parents=True, exist_ok=True)
            if text is not None:
                (root / rel / "cpu.max").write_text(text + "\n")
        deepest = max(quotas, key=len)
        proc = Path(tmp.name) / "proc-cgroup"
        proc.write_text(f"0::/{deepest}\n")
        return root, proc

    def test_the_tightest_quota_on_the_path_to_the_root_caps_the_affinity(self):
        root, proc = self.tree({
            "user.slice": "max 100000",
            "user.slice/user-1.slice": "800000 100000",  # CPUQuota=800%
            "user.slice/user-1.slice/session-1.scope": None,
        })  # fmt: skip
        with mock.patch.object(cpus, "affinity", return_value=48):
            self.assertEqual(cpus.usable(proc_cgroup=proc, root=root), 8)
        with mock.patch.object(cpus, "affinity", return_value=4):  # a cpuset tighter than the quota
            self.assertEqual(cpus.usable(proc_cgroup=proc, root=root), 4)

    def test_a_fractional_quota_rounds_up_and_never_below_one(self):
        root, proc = self.tree({"a": "150000 100000", "a/b": "20000 100000"})
        with mock.patch.object(cpus, "affinity", return_value=8):
            self.assertEqual(cpus.quotas("/a/b", root), [1, 2])
            self.assertEqual(cpus.usable(proc_cgroup=proc, root=root), 1)

    def test_no_quota_no_cgroup_v2_and_an_override(self):
        root, proc = self.tree({"x": "max 100000"})
        with mock.patch.object(cpus, "affinity", return_value=48):
            self.assertEqual(cpus.usable(proc_cgroup=proc, root=root), 48)
            self.assertEqual(cpus.usable(proc_cgroup=Path("/nonexistent"), root=root), 48)
            self.assertEqual(cpus.usable(["JOBS"], {"JOBS": "3"}, proc, root), 3)
            self.assertEqual(cpus.usable(["JOBS"], {"JOBS": "0"}, proc, root), 48, "0 is no choice")
            self.assertEqual(cpus.usable(["JOBS"], {"JOBS": "x"}, proc, root), 48)

    def test_this_process(self):
        self.assertGreaterEqual(cpus.usable(), 1)


class PerWorker(unittest.TestCase):
    """A Slurm task's workers: their share of the job's CPUs on the node."""

    def share(self, usable=64, **environ):
        with mock.patch.object(cpus, "usable", return_value=usable):
            return cpus.per_worker({k: str(v) for k, v in environ.items()})

    def test_cpus_per_task_when_given(self):
        self.assertEqual(self.share(SLURM_CPUS_PER_TASK=6, SLURM_CPUS_ON_NODE=48, SLURM_GPUS_ON_NODE=8), 6)
        self.assertEqual(self.share(usable=4, SLURM_CPUS_PER_TASK=6), 4, "never more than usable")

    def test_one_worker_per_gpu(self):
        """torchrun's workers, one per GPU of one task: the CPUs over the GPUs, never
        each worker all of them."""
        self.assertEqual(self.share(SLURM_CPUS_ON_NODE=64, SLURM_GPUS_ON_NODE=8, SLURM_TASKS_PER_NODE=1), 8)
        self.assertEqual(self.share(SLURM_CPUS_ON_NODE=12, SLURM_GPUS_ON_NODE=8), 1, "at least one")

    def test_one_worker_per_task(self):
        environ = dict(SLURM_CPUS_ON_NODE=32, SLURM_STEP_TASKS_PER_NODE="4(x2),2", SLURM_TASKS_PER_NODE=1)
        self.assertEqual(self.share(**environ, SLURM_NODEID=1), 8)
        self.assertEqual(self.share(**environ, SLURM_NODEID=2), 16)
        self.assertEqual(
            self.share(**environ, SLURM_NODEID=2, SLURM_GPUS_ON_NODE=4), 8, "more GPUs than tasks"
        )

    def test_outside_a_job(self):
        self.assertEqual(self.share(usable=8), 8)

    def test_a_builds_compilers_get_the_jobs_cpus_on_the_node(self):
        with mock.patch.object(cpus, "usable", return_value=64):
            self.assertEqual(cpus.per_node({"SLURM_CPUS_ON_NODE": "16", "SLURM_GPUS_ON_NODE": "8"}), 16)
            self.assertEqual(cpus.per_node({}), 64, "outside a job: usable")
        with mock.patch.object(cpus, "usable", return_value=4):
            self.assertEqual(cpus.per_node({"SLURM_CPUS_ON_NODE": "16"}), 4, "never more than usable")

    def test_the_command(self):
        with (
            mock.patch.object(cpus, "per_worker", return_value=3),
            mock.patch.object(cpus, "per_node", return_value=12),
            mock.patch("builtins.print") as out,
        ):
            self.assertEqual(cpus.main(["--job"]), 0)
        out.assert_called_once_with("3 12")
        with mock.patch.object(cpus, "usable", return_value=5) as usable, mock.patch("builtins.print") as out:
            cpus.main(["CHECK_JOBS"])
        usable.assert_called_once_with(["CHECK_JOBS"])
        out.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()
