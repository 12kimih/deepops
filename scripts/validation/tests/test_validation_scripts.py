"""Unit tests for the validation script parsers (standard library only).

Run with: python3 -m unittest discover scripts/validation/tests
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import unittest
from unittest import mock

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_slurm = load("validate_slurm")
validate_k8s = load("validate_k8s")
deepops_doctor = load("deepops_doctor")
validate_runai = load("validate_runai")


class TestSlurmParsers(unittest.TestCase):
    def test_sinfo_states_healthy(self):
        out = "node1 idle\nnode2 mixed\nnode3 allocated\n"
        total, avail, unavail, states = validate_slurm.parse_sinfo_states(out)
        self.assertEqual((total, avail, unavail), (3, 3, 0))
        self.assertEqual(states["idle"], 1)

    def test_sinfo_states_down_and_flags(self):
        out = "node1 idle\nnode2 down*\nnode3 drained\n"
        total, avail, unavail, states = validate_slurm.parse_sinfo_states(out)
        self.assertEqual((total, avail, unavail), (3, 1, 2))
        self.assertIn("down", states)
        self.assertIn("drained", states)

    def test_a_node_that_cannot_run_jobs_now_is_unavailable(self):
        # Not responding, powered off, rebooting, in maintenance: unavailable whatever
        # the base state says; a planned (-) or combined (+) node is still available.
        out = "n1 idle*\nn2 allocated*\nn3 mixed$\nn4 idle~\nn5 mixed-\nn6 idle+\nn7 idle@\n"
        total, avail, unavail, states = validate_slurm.parse_sinfo_states(out)
        self.assertEqual((total, avail, unavail), (7, 2, 5))
        self.assertEqual(states["idle"], 4)

    def test_sinfo_states_dedupes_partition_overlap(self):
        out = "node1 idle\nnode1 idle\n"
        total, _, _, _ = validate_slurm.parse_sinfo_states(out)
        self.assertEqual(total, 1)

    def test_gres_gpu_totals(self):
        out = "node1 gpu:4\nnode2 gpu:h100:8(S:0-1)\nnode3 (null)\n"
        self.assertEqual(validate_slurm.parse_gres_gpus(out), 12)

    def test_gres_dedupes_nodes(self):
        out = "node1 gpu:4\nnode1 gpu:4\n"
        self.assertEqual(validate_slurm.parse_gres_gpus(out), 4)

    def test_node_details_are_unique_normalized_and_sorted(self):
        states = "node-b down*\nnode-a IDLE+\nnode-b idle\n"
        gres = "node-b gpu:h100:8(S:0-1)\nnode-a gpu:2\nnode-b gpu:4\n"
        self.assertEqual(
            validate_slurm.build_node_details(states, gres),
            [
                {"name": "node-a", "state": "idle", "gpus_configured": 2},
                {"name": "node-b", "state": "down", "gpus_configured": 8},
            ],
        )

    def test_node_details_default_missing_gres_to_zero(self):
        self.assertEqual(
            validate_slurm.build_node_details("node1 mixed\n", ""),
            [{"name": "node1", "state": "mixed", "gpus_configured": 0}],
        )


class TestK8sParsers(unittest.TestCase):
    def test_summarize_nodes(self):
        doc = {
            "items": [
                {
                    "metadata": {"name": "node-a"},
                    "status": {
                        "conditions": [{"type": "Ready", "status": "True"}],
                        "allocatable": {"nvidia.com/gpu": "8"},
                    },
                },
                {
                    "metadata": {"name": "node-b"},
                    "status": {
                        "conditions": [{"type": "Ready", "status": "False"}],
                        "allocatable": {},
                    },
                },
            ]
        }
        total, ready, gpus, nodes = validate_k8s.summarize_nodes(doc)
        self.assertEqual((total, ready, gpus), (2, 1, 8))
        self.assertEqual(
            nodes,
            [
                {"name": "node-a", "ready": True, "gpus_allocatable": 8},
                {"name": "node-b", "ready": False, "gpus_allocatable": 0},
            ],
        )

    def test_summarize_nodes_details_are_sorted_and_malformed_gpus_are_zero(self):
        doc = {
            "items": [
                {
                    "metadata": {"name": "node-b"},
                    "status": {
                        "conditions": [{"type": "Ready", "status": "False"}],
                        "allocatable": {"nvidia.com/gpu": None},
                    },
                },
                {
                    "metadata": {"name": "node-a"},
                    "status": {
                        "conditions": [{"type": "Ready", "status": "True"}],
                        "allocatable": {"nvidia.com/gpu": "not-a-count"},
                    },
                },
                {
                    "metadata": {"name": "node-c"},
                    "status": {
                        "conditions": [{"type": "Ready", "status": "True"}],
                        "allocatable": {"nvidia.com/gpu": "4"},
                    },
                },
            ]
        }
        total, ready, gpus, nodes = validate_k8s.summarize_nodes(doc)
        self.assertEqual((total, ready, gpus), (3, 2, 4))
        self.assertEqual(
            nodes,
            [
                {"name": "node-a", "ready": True, "gpus_allocatable": 0},
                {"name": "node-b", "ready": False, "gpus_allocatable": 0},
                {"name": "node-c", "ready": True, "gpus_allocatable": 4},
            ],
        )

    def test_parse_gpu_count_rejects_other_malformed_values(self):
        for value in (True, -1, "-2", 1.5, [], {}):
            with self.subTest(value=value):
                self.assertEqual(validate_k8s.parse_gpu_count(value), 0)

    def test_summarize_gpu_pods(self):
        doc = {
            "items": [
                {
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [{"ready": True}],
                    }
                },
                {
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [{"ready": False}],
                    }
                },
                {"status": {"phase": "Succeeded"}},
                {"status": {"phase": "Pending"}},
            ]
        }
        total, ready = validate_k8s.summarize_gpu_pods(doc)
        self.assertEqual((total, ready), (4, 2))

    def test_smoke_pod_manifest_requests_one_gpu(self):
        pod = validate_k8s.smoke_pod_manifest("example/image:tag")
        limits = pod["spec"]["containers"][0]["resources"]["limits"]
        self.assertEqual(limits["nvidia.com/gpu"], 1)


class TestDoctorParsers(unittest.TestCase):
    def test_count_inventory_hosts(self):
        doc = {
            "_meta": {"hostvars": {"n1": {}, "n2": {}}},
            "all": {"children": ["slurm-master"]},
            "slurm-master": {"hosts": ["n1"]},
            "slurm-node": {"hosts": ["n2"]},
        }
        hosts, groups = deepops_doctor.count_inventory_hosts(doc)
        self.assertEqual(hosts, 2)
        self.assertIn("slurm-node", groups)

    def test_count_positive_stdout_hosts(self):
        out = (
            "n1 | CHANGED | rc=0 | (stdout) 2\n"
            "n2 | CHANGED | rc=0 | (stdout) 0\n"
            "n3 | CHANGED | rc=0 | (stdout) not-a-number\n"
        )
        self.assertEqual(deepops_doctor.count_positive_stdout_hosts(out), 1)


class TestRunDecodes(unittest.TestCase):
    def test_undecodable_output_is_replaced_not_raised(self):
        # A command printing bytes that are not UTF-8 must not turn --json into a traceback.
        cmd = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok \\xff')"]
        for module in (validate_slurm, validate_k8s, deepops_doctor, validate_runai):
            with self.subTest(module=module.__name__):
                rc, out, _ = module.run(cmd)
                self.assertEqual((rc, out), (0, "ok \ufffd"))


class TestSlurmGpuJob(unittest.TestCase):
    SINFO_GRES = "n1 gpu:a100:4(S:0-1)\nn2 gpu:h100:8\nn3 gpu:h100:8\nn3 gpu:h100:8\nn4 (null)\n"

    def test_gpu_types_count_each_node_once(self):
        self.assertEqual(validate_slurm.parse_gres_gpu_types(self.SINFO_GRES), {"a100": 4, "h100": 16})

    def test_the_type_with_the_most_gpus_is_picked(self):
        self.assertEqual(validate_slurm.pick_gpu_type(self.SINFO_GRES), "h100")
        self.assertEqual(validate_slurm.pick_gpu_type("n1 gpu:b:2\nn2 gpu:a:2\n"), "a")
        self.assertEqual(validate_slurm.pick_gpu_type("n1 gpu:4\n"), "")

    def test_the_job_is_named_and_typed(self):
        cmd = validate_slurm.gpu_job_command("deepops-validate-1", "h100", "batch")
        self.assertIn("--job-name=deepops-validate-1", cmd)
        self.assertIn("--gpus=h100:1", cmd)
        self.assertEqual(cmd[cmd.index("--partition") + 1], "batch")
        self.assertIn("--gpus=1", validate_slurm.gpu_job_command("x", "", ""))

    def main(self, argv, srun_rc):
        calls = []

        def fake_run(cmd, timeout=60):
            calls.append(cmd)
            if cmd[0] == "srun":
                return (srun_rc, "NVIDIA H100, 580.95", "") if srun_rc == 0 else (124, "", "timeout after 1s")
            if cmd[:2] == ["sinfo", "--version"]:
                return 0, "slurm 26.05.1", ""
            if cmd[-1] == "%n %T":
                return 0, "n1 idle\nn2 idle\n", ""
            if cmd[-1] == "%n %G":
                return 0, self.SINFO_GRES, ""
            return 0, "", ""

        out = io.StringIO()
        with (
            mock.patch.object(validate_slurm, "run", fake_run),
            mock.patch.object(validate_slurm.shutil, "which", return_value="/usr/bin/sinfo"),
            mock.patch.object(sys, "argv", ["validate_slurm.py", "--json"] + argv),
            contextlib.redirect_stdout(out),
        ):
            rc = validate_slurm.main()
        return rc, json.loads(out.getvalue()), calls

    def test_a_timed_out_job_is_cancelled_by_its_name(self):
        rc, result, calls = self.main(["--gpu-type", "a100"], srun_rc=124)
        self.assertEqual(rc, 1)
        self.assertFalse(result["gpu_job_ok"])
        srun = next(c for c in calls if c[0] == "srun")
        self.assertIn("--gpus=a100:1", srun)
        name = next(a for a in srun if a.startswith("--job-name="))[len("--job-name=") :]
        scancel = next(c for c in calls if c[0] == "scancel")
        self.assertEqual(scancel[:3], ["scancel", "--name", name])

    def test_a_job_that_ran_is_not_cancelled(self):
        rc, result, calls = self.main([], srun_rc=0)
        self.assertEqual(rc, 0)
        self.assertTrue(result["gpu_job_ok"])
        self.assertIn("--gpus=h100:1", next(c for c in calls if c[0] == "srun"))
        self.assertFalse([c for c in calls if c[0] == "scancel"])


if __name__ == "__main__":
    unittest.main()
