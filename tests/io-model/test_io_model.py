"""scripts/deepops/io_model.py on a real fio 3.36 result (a short run of the job, the
fixture): the model's coefficients come from the right patterns, the latency targets
from the random p95s, and a run the model must not be made from is refused."""

import copy
import datetime
import json
import os
import sys
import time
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import io_model  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fio-3.36.json").read_text())
FIXTURE["global options"]["direct"] = "1"  # the fixture's tiny run used the page cache
JOBS = {j["jobname"]: j for j in FIXTURE["jobs"]}


class Model(unittest.TestCase):
    def test_each_coefficient_from_its_pattern(self):
        model = io_model.entry(FIXTURE)["model"]
        self.assertEqual(model["rbps"], int(JOBS["seq-read"]["read"]["bw_bytes"]))
        self.assertEqual(model["wbps"], int(JOBS["seq-write"]["write"]["bw_bytes"]))
        self.assertEqual(model["rseqiops"], int(JOBS["seq-read-4k"]["read"]["iops"]))
        self.assertEqual(model["wrandiops"], int(JOBS["rand-write"]["write"]["iops"]))

    def test_latency_targets_are_twice_the_random_p95(self):
        qos = io_model.entry(FIXTURE)["qos"]
        p95 = JOBS["rand-read"]["read"]["clat_ns"]["percentile"]["95.000000"]
        self.assertEqual(qos["rlat"], int(2 * p95 / 1000))
        self.assertEqual((qos["rpct"], qos["wpct"], qos["min"], qos["max"]), (95, 95, 50, 150))

    def test_the_config_block_reads_back(self):
        text = io_model.as_yaml("/home", io_model.entry(FIXTURE, "fio --output-format=json job.fio"))
        item = yaml.safe_load(text)["io_fairness_models"]["/home"]
        self.assertEqual(item["command"], "fio --output-format=json job.fio")
        self.assertEqual(set(item["model"]), set(io_model.MODEL))
        self.assertEqual(set(item["latency"]), set(io_model.PATTERNS))
        self.assertRegex(item["measured"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d[+-]\d\d:\d\d$")

    def test_the_run_is_dated_in_local_time_with_its_offset(self):
        # A run that ended at 00:32 local time, nine hours ahead of UTC, was dated the
        # day before (the UTC date).
        old = os.environ.get("TZ")
        self.addCleanup(lambda: (os.environ.pop("TZ", None) if old is None else os.environ.__setitem__("TZ", old),
                                 time.tzset()))  # fmt: skip
        os.environ["TZ"] = "XST-9"
        time.tzset()
        ended = int(datetime.datetime(2026, 11, 23, 15, 32, tzinfo=datetime.timezone.utc).timestamp())
        item = io_model.entry({**FIXTURE, "timestamp": ended})
        self.assertEqual(item["measured"], "2026-11-24T00:32+09:00")


class Refused(unittest.TestCase):
    def refused(self, result, why):
        with self.assertRaises(io_model.Refused) as caught:
            io_model.entry(result)
        self.assertIn(why, str(caught.exception))

    def test_the_page_cache_is_not_the_device(self):
        result = copy.deepcopy(FIXTURE)
        result["global options"]["direct"] = "0"
        self.refused(result, "direct=1")

    def test_a_missing_or_failed_pattern(self):
        result = copy.deepcopy(FIXTURE)
        result["jobs"] = [j for j in result["jobs"] if j["jobname"] != "rand-write"]
        self.refused(result, "rand-write")
        result = copy.deepcopy(FIXTURE)
        result["jobs"][0]["error"] = 5
        self.refused(result, "failed")

    def test_no_percentiles(self):
        result = copy.deepcopy(FIXTURE)
        result["jobs"][4]["read"]["clat_ns"] = {"mean": 1}
        self.refused(result, "p50/p95/p99")

    def test_the_cli_says_why_and_fails(self):
        self.assertEqual(io_model.main([str(Path(__file__).parent / "fio-3.36.json"), "/home"]), 1)


if __name__ == "__main__":
    unittest.main()
