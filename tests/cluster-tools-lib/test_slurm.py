"""Unit tests for clustertools.slurm (roles/cluster_tools_lib), no cluster needed."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from clustertools import slurm  # noqa: E402


def value(n):
    return {"set": True, "infinite": False, "number": n}


class HostlistTests(unittest.TestCase):
    def test_ranges_lists_padding_and_two_brackets(self):
        self.assertEqual(
            slurm.hostnames("gpu[1,3-5],n[08-10],rack[1-2]-c[1-2],solo"),
            [
                "gpu1",
                "gpu3",
                "gpu4",
                "gpu5",
                "n08",
                "n09",
                "n10",
                "rack1-c1",
                "rack1-c2",
                "rack2-c1",
                "rack2-c2",
                "solo",
            ],
        )

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
        self.assertEqual(
            slurm.job_label({"job_id": 5, "array_job_id": value(4), "array_task_id": value(0)}), "4_0"
        )

    def test_label_of_a_pending_array_record(self):
        record = {
            "job_id": 9,
            "array_job_id": value(9),
            "array_task_id": {"set": False},
            "array_task_string": "0-11%1",
        }
        self.assertEqual(slurm.job_label(record), "9_[0-11%1]")

    def test_gpus_per_node_and_mismatch(self):
        job = {"job_id": 1, "nodes": "gpu[1-2]", "gres_detail": ["gpu:a10:1(IDX:5)", "gpu:a10:2(IDX:0-1)"]}
        self.assertEqual(slurm.job_gpus(job), ([("gpu1", 5), ("gpu2", 0), ("gpu2", 1)], {"a10"}))
        job["gres_detail"] = ["gpu:a10:1(IDX:5)"]
        self.assertEqual(slurm.job_gpus(job), ([], set()))


class ArrayTests(unittest.TestCase):
    def test_task_counts(self):
        for spec, n in (
            ("1-10%2", 10),
            ("1-9:2", 5),
            ("3,5,7-8", 4),
            ("", 1),
            ("39%2", 1),
            ("0-11%1", 12),
            ("4", 1),
        ):
            with self.subTest(spec=spec):
                self.assertEqual(slurm.array_tasks(spec), n)


FIXTURES = Path(__file__).resolve().parents[1] / "cluster-tools/fixtures"
FIXTURE_FILE = {
    "nodes": "nodes",
    "partitions": "partitions",
    "jobs": "jobs",
    "jobs_detail": "jobs",
    "qos": "qos",
    "associations": "assoc",
}


class SchemaTests(unittest.TestCase):
    """FIELDS against every Slurm release we keep a fixture for. After an upgrade, a new
    fixture that fails here names exactly the fields that release renamed."""

    def releases(self):
        found = sorted(FIXTURES.glob("slurm-*"))
        self.assertTrue(found, "no fixtures")
        return found

    def test_every_fixture_has_every_field_the_tools_read(self):
        for release in self.releases():
            for kind, name in FIXTURE_FILE.items():
                with self.subTest(release=release.name, kind=kind):
                    data = json.loads((release / f"{name}.json").read_text())
                    slurm.check(kind, data)
                    self.assertTrue(data[slurm.QUERIES[kind][1]], "fixture has no records")

    def test_every_query_has_fields_and_a_fixture(self):
        self.assertEqual(set(slurm.QUERIES), set(slurm.FIELDS))
        self.assertEqual(set(slurm.QUERIES), set(FIXTURE_FILE))

    def test_a_renamed_field_is_named(self):
        data = json.loads((self.releases()[0] / "jobs.json").read_text())
        data["meta"]["slurm"]["release"] = "26.05.0"
        data["jobs"][0]["user"] = data["jobs"][0].pop("user_name")  # as if renamed upstream
        with self.assertRaises(slurm.SchemaError) as ctx:
            slurm.check("jobs", data)
        self.assertIn("Slurm 26.05.0 jobs lack user_name;", str(ctx.exception))

    def test_an_error_inside_the_reply_is_raised(self):
        data = {"meta": {}, "errors": [{"description": "Unable to contact slurm controller"}], "jobs": []}
        with self.assertRaisesRegex(slurm.SchemaError, "Unable to contact slurm controller"):
            slurm.check("jobs", data)

    def test_a_missing_list_is_raised(self):
        with self.assertRaisesRegex(slurm.SchemaError, "no 'nodes' list"):
            slurm.check("nodes", {"meta": {}})


class FailureTests(unittest.TestCase):
    def test_one_line_for_each_kind_of_failure(self):
        import subprocess

        err = subprocess.CalledProcessError(1, ("scontrol",), stderr="slurm_load_jobs error: down\nmore")
        self.assertEqual(slurm.failure(err), "slurm_load_jobs error: down")
        self.assertEqual(
            slurm.failure(subprocess.CalledProcessError(2, ("sinfo",))), "sinfo exited with status 2"
        )
        self.assertIn("no answer in", slurm.failure(subprocess.TimeoutExpired(("scontrol",), 20)))


class TresTests(unittest.TestCase):
    def test_list_and_string(self):
        self.assertEqual(
            slurm.tres_list(
                [{"type": "gres", "name": "gpu:a10", "count": 6}, {"type": "cpu", "name": "", "count": 8}]
            ),
            {"gres/gpu:a10": 6, "cpu": 8},
        )
        tres = slurm.tres_str("cpu=8,gres/gpu:a10=4,gres/gpu=2")
        self.assertEqual((tres["gres/gpu"], tres["gres/gpu:a10"]), ("2", "4"))
        self.assertEqual(slurm.tres_str(""), {})

    def test_memory_units(self):
        for text, mib in (
            ("960G", 983040),
            ("1.50T", 1572864),
            ("512M", 512),
            ("2048K", 2),
            ("515616", 515616),
            ("", 0),
            ("x", 0),
        ):
            with self.subTest(text=text):
                self.assertEqual(slurm.mem_mib(text), mib)

    def test_cluster_name(self):
        self.assertEqual(slurm.cluster_name({"meta": {"slurm": {"cluster": "c1"}}}), "c1")
        self.assertEqual(slurm.cluster_name({}), "")


if __name__ == "__main__":
    unittest.main()
