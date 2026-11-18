"""Unit tests for clustertools.slurm (roles/cluster_tools_lib), no cluster needed."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from clustertools import slurm  # noqa: E402


def value(n):
    return {"set": True, "infinite": False, "number": n}


class HostlistTests(unittest.TestCase):
    def test_ranges_lists_padding_and_two_brackets(self):
        self.assertEqual(slurm.hostnames("gpu[1,3-5],n[08-10],rack[1-2]-c[1-2],solo"),
                         ["gpu1", "gpu3", "gpu4", "gpu5", "n08", "n09", "n10",
                          "rack1-c1", "rack1-c2", "rack2-c1", "rack2-c2", "solo"])

    def test_empty_forms(self):
        for empty in ("", "(null)", "None assigned", None):
            self.assertEqual(slurm.hostnames(empty), [])

    def test_plain_list(self):
        self.assertEqual(slurm.hostnames("gpu1,gpu2"), ["gpu1", "gpu2"])

    def test_expand_ranges(self):
        self.assertEqual(slurm.expand_ranges("0-2,5"), [0, 1, 2, 5])


class NumberTests(unittest.TestCase):
    def test_slurm_number_objects(self):
        self.assertEqual(slurm.num(value(7)), 7)
        self.assertIsNone(slurm.num({"set": False, "infinite": False, "number": 0}))
        self.assertIsNone(slurm.num({"set": True, "infinite": True, "number": 0}))
        self.assertEqual(slurm.num(3), 3)


class JobTests(unittest.TestCase):
    def test_label(self):
        self.assertEqual(slurm.job_label({"job_id": 5, "array_job_id": value(0)}), "5")
        self.assertEqual(slurm.job_label({"job_id": 5, "array_job_id": value(4),
                                          "array_task_id": value(0)}), "4_0")

    def test_gpus_per_node_and_mismatch(self):
        job = {"job_id": 1, "nodes": "gpu[1-2]", "gres_detail": ["gpu:a10:1(IDX:5)", "gpu:a10:2(IDX:0-1)"]}
        self.assertEqual(slurm.job_gpus(job), ([("gpu1", 5), ("gpu2", 0), ("gpu2", 1)], {"a10"}))
        job["gres_detail"] = ["gpu:a10:1(IDX:5)"]
        self.assertEqual(slurm.job_gpus(job), ([], set()))


if __name__ == "__main__":
    unittest.main()
