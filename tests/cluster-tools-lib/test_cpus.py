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


if __name__ == "__main__":
    unittest.main()
