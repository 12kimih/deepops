"""Unit tests for the validation script parsers (standard library only).

Run with: python3 -m unittest discover scripts/validation/tests
"""

import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import unittest
from unittest import mock

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)  # their shared modules (role_defaults), as when run as scripts


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_slurm = load("validate_slurm")
validate_k8s = load("validate_k8s")
deepops_doctor = load("deepops_doctor")
validate_runai = load("validate_runai")


def node(name, state, gres=""):
    """A node record as scontrol --json gives it, the fields validate_slurm reads."""
    return {"name": name, "state": state, "gres": gres}


class TestSlurmNodes(unittest.TestCase):
    def test_a_healthy_cluster_is_all_available(self):
        nodes = [node("n1", ["IDLE"]), node("n2", ["MIXED"]), node("n3", ["ALLOCATED"])]
        _, states, unavailable = validate_slurm.summarize_nodes(nodes)
        self.assertEqual(unavailable, [])
        self.assertEqual(states, {"idle": 1, "mixed": 1, "allocated": 1})

    def test_a_node_no_job_can_start_on_is_unavailable(self):
        # The cluster tools' one definition (clustertools.vocab.node_schedulable):
        # down, not responding, drained, draining or in maintenance is out; powered
        # down (Slurm powers it up for a job) and reserved are in.
        nodes = [
            node("n1", ["IDLE", "NOT_RESPONDING"]),
            node("n2", ["DOWN"]),
            node("n3", ["MIXED", "DRAIN"]),
            node("n4", ["IDLE", "POWERED_DOWN"]),
            node("n5", ["IDLE", "RESERVED"]),
            node("n6", ["IDLE", "MAINTENANCE", "RESERVED"]),
        ]
        _, states, unavailable = validate_slurm.summarize_nodes(nodes)
        self.assertEqual(unavailable, ["n1", "n2", "n3", "n6"])
        self.assertEqual(states["mixed+drain"], 1)

    def test_details_are_sorted_with_each_nodes_gpus(self):
        nodes = [node("node-b", ["DOWN"], "gpu:h100:8(S:0-1)"), node("node-a", ["IDLE"], "gpu:2,gpu:t4:1")]
        details, _, _ = validate_slurm.summarize_nodes(nodes)
        self.assertEqual(
            details,
            [
                {"name": "node-a", "state": "idle", "gpus_configured": 3},
                {"name": "node-b", "state": "down", "gpus_configured": 8},
            ],
        )

    def test_the_fixture_of_every_slurm_release_reads(self):
        # The same replies the cluster tools are tested on, one per Slurm release.
        root = os.path.dirname(os.path.dirname(SCRIPTS_DIR))
        fixtures = os.path.join(root, "tests", "cluster-tools", "fixtures")
        releases = sorted(
            d for d in os.listdir(fixtures) if os.path.isfile(os.path.join(fixtures, d, "nodes.json"))
        )
        self.assertTrue(releases)
        for release in releases:
            with self.subTest(release=release), open(os.path.join(fixtures, release, "nodes.json")) as f:
                nodes = validate_slurm.slurm.check("nodes", json.load(f))["nodes"]
                details, states, _ = validate_slurm.summarize_nodes(nodes)
                self.assertEqual(len(details), len(nodes))
                self.assertEqual(sum(states.values()), len(nodes))


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
        results = {"n1": {"stdout": "2"}, "n2": {"stdout": "0"}, "n3": {"stdout": "not-a-number"}, "n4": {}}
        self.assertEqual(deepops_doctor.count_positive_stdout_hosts(results), 1)

    def test_reached_hosts(self):
        results = {"n1": {"ping": "pong"}, "n2": {"unreachable": True}, "n3": {"failed": True}}
        self.assertEqual(deepops_doctor.reached(results), ["n1"])


class TestRunDecodes(unittest.TestCase):
    def test_undecodable_output_is_replaced_not_raised(self):
        # A command printing bytes that are not UTF-8 must not turn --json into a traceback.
        cmd = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok \\xff')"]
        for module in (validate_slurm, validate_k8s, deepops_doctor, validate_runai):
            with self.subTest(module=module.__name__):
                rc, out, _ = module.run(cmd)
                self.assertEqual((rc, out), (0, "ok \ufffd"))


class TestSlurmGpuJob(unittest.TestCase):
    NODES = [
        node("n1", ["IDLE"], "gpu:a100:4(S:0-1)"),
        node("n2", ["IDLE"], "gpu:h100:8"),
        node("n3", ["IDLE"], "gpu:h100:8"),
        node("n4", ["IDLE"], "(null)"),
    ]

    def test_gpu_types_add_up_over_the_nodes(self):
        self.assertEqual(validate_slurm.gpu_types(self.NODES), {"a100": 4, "h100": 16})

    def test_the_type_with_the_most_gpus_is_picked(self):
        self.assertEqual(validate_slurm.pick_gpu_type(self.NODES), "h100")
        self.assertEqual(
            validate_slurm.pick_gpu_type([node("n1", ["IDLE"], "gpu:b:2"), node("n2", ["IDLE"], "gpu:a:2")]),
            "a",
        )
        self.assertEqual(validate_slurm.pick_gpu_type([node("n1", ["IDLE"], "gpu:4")]), "")

    def test_the_job_is_named_and_typed(self):
        cmd = validate_slurm.gpu_job_command("deepops-validate-1", "h100", "batch")
        self.assertIn("--job-name=deepops-validate-1", cmd)
        self.assertIn("--gpus=h100:1", cmd)
        self.assertEqual(cmd[cmd.index("--partition") + 1], "batch")
        self.assertIn("--gpus=1", validate_slurm.gpu_job_command("x", "", ""))

    def main(self, argv, srun_rc, job_out="NVIDIA H100, 580.95\nprivate_tmp=1\nvulkan=1\negl=1", config=""):
        calls = []

        def fake_run(cmd, timeout=60):
            calls.append(cmd)
            if cmd[0] == "srun":
                return (srun_rc, job_out, "") if srun_rc == 0 else (124, "", "timeout after 1s")
            if cmd[:3] == ["scontrol", "show", "config"]:
                return 0, config, ""
            return 0, "", ""

        out = io.StringIO()
        with (
            mock.patch.object(validate_slurm, "run", fake_run),
            mock.patch.object(validate_slurm.slurm, "load", return_value={"nodes": self.NODES}),
            mock.patch.object(validate_slurm.shutil, "which", return_value="/usr/bin/scontrol"),
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

    def test_the_job_reports_whether_its_tmp_was_its_own(self):
        ns = "NamespaceType           = namespace/linux\n"
        rc, result, _ = self.main([], 0, config=ns)
        self.assertEqual((rc, result["private_tmp_expected"], result["private_tmp_in_job"]), (0, True, True))
        self.assertEqual(result["gpus_visible_in_job"], 1)
        rc, result, _ = self.main(
            [], 0, job_out="NVIDIA H100, 580.95\nprivate_tmp=0\nvulkan=1\negl=1", config=ns
        )
        self.assertEqual(rc, 1)
        self.assertIn("/tmp was not its own", " ".join(result["failures"]))
        rc, result, _ = self.main(
            [],
            0,
            job_out="NVIDIA H100, 580.95\nprivate_tmp=0\nvulkan=1\negl=1",
            config="NamespaceType = (null)\n",
        )
        self.assertEqual(
            (rc, result["private_tmp_expected"], result["private_tmp_in_job"]), (0, False, False)
        )

    def test_the_job_must_see_the_gpu_graphics_driver_unless_headless(self):
        # The R580 switch to the headless driver took GPU Vulkan from every node and
        # nothing noticed: rendering fell back to the CPU without an error.
        headless = "NVIDIA H100, 580.95\nprivate_tmp=1\nvulkan=0\negl=0"
        rc, result, _ = self.main([], 0, job_out=headless)
        self.assertEqual((rc, result["graphics_expected"], result["graphics_in_job"]), (1, True, False))
        self.assertIn("no NVIDIA Vulkan driver", " ".join(result["failures"]))
        rc, result, _ = self.main(["--headless"], 0, job_out=headless)
        self.assertEqual((rc, result["graphics_expected"]), (0, False))
        rc, result, _ = self.main([], 0, job_out="NVIDIA H100, 580.95\nprivate_tmp=1\nvulkan=1\negl=0")
        self.assertEqual((rc, result["graphics_in_job"]), (1, False), "Vulkan without EGL is not enough")
        self.assertEqual(self.main([], 0)[1]["gpus_visible_in_job"], 1, "probe lines are not GPUs")

    def test_the_job_checks_its_tmp_after_its_gpus(self):
        script = validate_slurm.gpu_job_command("x", "", "")[-1]
        self.assertTrue(script.startswith("nvidia-smi "))
        self.assertIn("findmnt -n -o FSROOT --mountpoint /tmp", script)
        self.assertIn('"/$SLURM_JOB_ID/"', script)
        for key in validate_slurm.PROBES:
            self.assertIn('printf "%s=%%s' % key, script)


if __name__ == "__main__":
    unittest.main()


class TestValidatorsStartProcessesOneWay(unittest.TestCase):
    """A validator's one-shot command goes through clustertools.proc (bounded, its whole
    session killed on a timeout, undecodable bytes replaced, never raising): six copies of
    a run() helper had drifted, one without errors="replace" and one without a timeout
    message. Popen stays for what must outlive a call (a port-forward, a stream)."""

    def test_no_validator_runs_a_command_itself(self):
        one_shot = re.compile(
            r"\bsubprocess\.(run|call|check_call|check_output|getoutput|getstatusoutput)\b|\bos\.(system|popen)\b"
        )
        offenders = [
            f"{name}:{n}"
            for name in sorted(os.listdir(SCRIPTS_DIR))
            if name.endswith(".py")
            for n, line in enumerate(open(os.path.join(SCRIPTS_DIR, name)).read().splitlines(), 1)
            if one_shot.search(line)
        ]
        self.assertEqual(offenders, [], "run commands with clustertools.proc (outcome or run)")
