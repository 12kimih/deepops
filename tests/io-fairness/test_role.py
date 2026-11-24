"""roles/io_fairness: the configuration it writes is one cluster_tools.io_fairness takes, built
from the site's models as scripts/deepops/io_model.py prints them, and its slices'
weights and boot unit say what the defaults promise."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
from ansible_yaml import load, render, role_defaults, same_name  # noqa: E402
from cluster_tools import io_fairness as iocost  # noqa: E402

# An entry exactly as io_model.py prints it (tests/io-model's fixture shape).
ENTRY = yaml.safe_load("""
measured: "2026-01-01T00:32+09:00"
command: "sudo fio --output-format=json --output=r.json io-model.fio"
model: {rbps: 1000000000, rseqiops: 50000, rrandiops: 1500, wbps: 800000000, wseqiops: 500, wrandiops: 450}
qos: {rpct: 95, rlat: 200000, wpct: 95, wlat: 400000, min: 50, max: 150}
latency:
  seq-read: {p50: 40.0, p95: 120.0, p99: 180.0}
""")
VARS = yaml.safe_load((ROOT / "roles/io_fairness/vars/main.yml").read_text())


def values(**overrides):
    exports = [{"path": m} for m in overrides.get("io_fairness_models", {})]
    return {**role_defaults("io_fairness"), **VARS, "node_exporter_prom_dir": "/run/prom", "nfs_exports": exports,
            "cluster_tools_lib_config_records": "/etc/cluster-tools/configs.d", **overrides}  # fmt: skip


def rendered(**overrides):
    return render(VARS["_io_fairness_cfg"], values(**overrides))


class Config(unittest.TestCase):
    def test_the_written_config_is_one_the_tool_takes(self):
        cfg = rendered(io_fairness_models={"/home": ENTRY, "/data": ENTRY}, io_fairness_enable=True)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "io-fairness.json")
            path.write_text(json.dumps(cfg))
            loaded = iocost.load_config(path)
        self.assertEqual([dev["mount"] for dev in loaded["devices"]], ["/data", "/home"])
        self.assertEqual(loaded["devices"][1], {"mount": "/home", "enable": True, "model": ENTRY["model"],
                                                "qos": ENTRY["qos"]})  # fmt: skip

    def test_off_by_default(self):
        self.assertFalse(role_defaults("io_fairness")["io_fairness_enable"])
        self.assertFalse(rendered(io_fairness_models={"/home": ENTRY})["devices"][0]["enable"])

    def test_an_entry_without_its_model_is_refused_before_a_host(self):
        with self.assertRaises(Exception) as caught:
            rendered(io_fairness_models={"/home": {"measured": "x"}})
        self.assertIn("/home has no model or qos", str(caught.exception))


class Hosts(unittest.TestCase):
    """A model is the disk under one export: only the server holding it sets it. The
    same path on another NFS server is a mount of the first (ARA 298: /home's model on
    the /scratch server failed its boot unit)."""

    def test_each_server_sets_the_models_of_its_own_exports(self):
        models = {"/home": ENTRY, "/scratch": ENTRY}
        cfg = rendered(io_fairness_models=models, nfs_exports=[{"path": "/home/"}, {"path": "/sw"}])
        self.assertEqual([dev["mount"] for dev in cfg["devices"]], ["/home"])

    def test_a_server_with_no_measured_export_converges_as_absent(self):
        v = values(io_fairness_models={"/home": ENTRY}, nfs_exports=[{"path": "/scratch"}])
        self.assertEqual(render(VARS["_io_fairness_state"], v), "absent")
        self.assertEqual(render(VARS["_io_fairness_state"], {**v, "io_fairness_state": "kept"}), "kept")
        v = values(io_fairness_models={"/home": ENTRY})
        self.assertEqual(render(VARS["_io_fairness_state"], v), "present")

    def test_the_exports_are_the_nfs_servers_own_setting(self):
        self.assertIn("nfs_exports", role_defaults("nfs_common"))
        deps = [d["role"] for d in load("roles/io_fairness/meta/main.yml")["dependencies"]]
        self.assertIn("nfs_common", deps)


class Notices(unittest.TestCase):
    def test_the_io_notices_measure_a_node_against_the_disks_throughput(self):
        mounts = render("{{ models | io_notice_mounts }}", {"models": {"/home": ENTRY}})
        self.assertEqual(mounts, {"/home": {"read_bps": 1000000000, "write_bps": 800000000}})
        self.assertEqual(render("{{ {} | io_notice_mounts }}", {}), {})


class Weights(unittest.TestCase):
    def test_services_before_logins_and_everyone_equal_within(self):
        defaults = role_defaults("io_fairness")
        weights = defaults["io_fairness_slice_weights"]
        self.assertGreater(weights["system.slice"], weights["user.slice"])
        task = next(
            t
            for t in load("roles/io_fairness/tasks/present.yml")
            if same_name(t.get("name"), "Weight the slices")
        )
        items = render(task["loop"], defaults)
        self.assertEqual({i["key"]: int(i["value"]) for i in items},
                         {"system.slice": 100, "user.slice": 50, "user-.slice": 100})  # fmt: skip
        removed = render(VARS["_io_fairness_dropins"], {**defaults, **VARS})
        self.assertEqual(
            sorted(Path(p).parent.name for p in removed), ["system.slice.d", "user-.slice.d", "user.slice.d"]
        )


class Revert(unittest.TestCase):
    def test_absent_turns_iocost_off_before_its_command_goes(self):
        names = [t.get("name") for t in load("roles/io_fairness/tasks/absent.yml")]
        self.assertLess(names.index("Turn iocost off"), names.index("Remove what this component installed"))
        reset = load("roles/io_fairness/tasks/absent.yml")[0]
        self.assertEqual(reset["command"]["argv"][1], "reset")


if __name__ == "__main__":
    unittest.main()
