#!/usr/bin/env python3
"""Inert configless, GPU auto-detection and metrics regressions; run with Ansible's Python.

The role's slurm.conf and gres.conf are rendered in memory from the role defaults with
config.example's Slurm settings over them, and the controller's own check of a
slurm.conf template is evaluated against good and bad files. No host is contacted.
"""

import base64
import os
from pathlib import Path
import sys
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from ansible_yaml import condition, load, render, role_defaults, same_name  # noqa: E402

ROOT = Path(os.environ.get("DEEPOPS_TEST_ROOT", Path(__file__).resolve().parents[2]))
ROLE = ROOT / "roles/slurm"
DEFAULTS = role_defaults("slurm")
EXAMPLE = yaml.safe_load((ROOT / "config.example/group_vars/slurm-cluster.yml").read_text())
FACTS = {
    "ansible_facts": {
        "architecture": "x86_64",
        "ansible_local": {
            "memory": {"total_mb": 1000},
            "topology": {
                "cpu_topology": {
                    "sockets": "1",
                    "cores_per_socket": "4",
                    "threads_per_core": "2",
                    "logical_cpus": "8",
                },
                "gpu_topology": ["0-3", "4-7"],
            },
        },
    }
}
GROUPS = {"slurm-master": ["ctl01", "ctl02"], "slurm-node": ["node01"]}


def variables(**overrides):
    return {
        **DEFAULTS,
        **EXAMPLE,
        **FACTS,
        "inventory_dir": str(ROOT / "config.example"),
        "groups": GROUPS,
        "hostvars": {"node01": FACTS},
        **overrides,
    }


def rendered(template_var, **overrides):
    values = variables(**overrides)
    return render((ROLE / "templates" / values[template_var]).read_text(), values) or ""


def settings(conf):
    """slurm.conf key=value lines (the last wins, as in Slurm)."""
    found = {}
    for line in conf.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line and " " not in line.split("=", 1)[0]:
            key, value = line.split("=", 1)
            found[key] = value
    return found


def task(path, name):
    found = [t for t in load(path) if same_name(t.get("name"), name)]
    assert len(found) == 1, name
    return found[0]


class SlurmConf(unittest.TestCase):
    def test_controller_serves_the_configuration_and_metrics(self):
        conf = settings(rendered("slurm_conf_template"))
        self.assertIn("enable_configless", conf["SlurmctldParameters"].split(","))
        self.assertEqual(conf["MetricsType"], "metrics/openmetrics")
        self.assertEqual(conf["SlurmctldPort"], str(DEFAULTS["slurm_slurmctld_port"]))
        self.assertEqual(conf["MpiDefault"], "pmix")

    def test_health_check_reaches_allocated_nodes(self):
        conf = settings(rendered("slurm_conf_template", slurm_health_check_program="/usr/sbin/nhc"))
        self.assertEqual(conf["HealthCheckNodeState"], "ANY,CYCLE")
        self.assertIn("HealthCheckInterval", conf)

    def test_cluster_name_is_the_setting(self):
        conf = settings(rendered("slurm_conf_template", slurm_cluster_name="renamed"))
        self.assertEqual(conf["ClusterName"], "renamed")

    def test_packaged_plugin_dir_and_runtime_dirs(self):
        conf = settings(rendered("slurm_conf_template"))
        self.assertNotIn("PluginDir", conf)
        self.assertTrue(conf["SlurmctldPidFile"].startswith("/run/slurmctld/"))
        self.assertTrue(conf["SlurmdPidFile"].startswith("/run/slurm/"))
        # Read, not rendered: it uses the repository's required_secret filter.
        dbd = settings((ROLE / "templates" / DEFAULTS["slurm_dbd_conf_template"]).read_text())
        self.assertNotIn("PluginDir", dbd)
        self.assertTrue(dbd["PidFile"].startswith("/run/slurmdbd/"))


class GresConf(unittest.TestCase):
    def test_gpus_are_auto_detected_never_listed(self):
        lines = [x for x in rendered("slurm_gres_conf_template").splitlines() if x and not x.startswith("#")]
        self.assertEqual(lines, ["AutoDetect=nvml"])

    def test_no_gpu_management_no_gres(self):
        lines = [
            x for x in rendered("slurm_gres_conf_template", slurm_manage_gpus=False).splitlines() if x.strip()
        ]
        self.assertEqual(lines, [])


class ControllerCheck(unittest.TestCase):
    """The controller's check of slurm_conf_template (controller.yml)."""

    CHECK = task("roles/slurm/tasks/controller.yml", "Check the slurm.conf template")

    def passes(self, conf, **overrides):
        values = variables(**overrides)
        values.update(self.CHECK["vars"])
        values["_conf"] = conf
        return condition(self.CHECK["assert"]["that"], values)

    def test_role_template_passes(self):
        self.assertTrue(self.passes(rendered("slurm_conf_template")))

    def test_refuses_a_template_without_configless(self):
        conf = rendered("slurm_conf_template").replace(
            "SlurmctldParameters=enable_configless", "SlurmctldParameters=rl_enable"
        )
        self.assertFalse(self.passes(conf))

    def test_configless_among_other_parameters(self):
        conf = rendered("slurm_conf_template").replace(
            "SlurmctldParameters=enable_configless",
            "SlurmctldParameters=rl_enable,enable_configless,rl_bucket_size=50",
        )
        self.assertTrue(self.passes(conf))

    def test_refuses_another_cluster_name(self):
        self.assertFalse(self.passes(rendered("slurm_conf_template"), slurm_cluster_name="other"))
        conf = rendered("slurm_conf_template")
        self.assertFalse(
            self.passes(
                conf.replace("ClusterName=deepops", "ClusterName=hardcoded"), slurm_cluster_name="deepops"
            )
        )

    def test_refuses_the_source_build_plugin_dir(self):
        conf = rendered("slurm_conf_template") + "\nPluginDir=/usr/local/lib/slurm\n"
        self.assertFalse(self.passes(conf))
        conf = rendered("slurm_conf_template") + "\nPluginDir=/usr/lib/x86_64-linux-gnu/slurm\n"
        self.assertTrue(self.passes(conf))


class JobPrivateDirs(unittest.TestCase):
    """slurm_job_private_dirs alone decides the namespace plugin, its file and Contain."""

    CHECK = ControllerCheck.CHECK

    def test_on_by_default_with_its_file(self):
        conf = settings(rendered("slurm_conf_template"))
        self.assertEqual(conf["NamespaceType"], "namespace/linux")
        self.assertIn("Contain", conf["PrologFlags"].split(","))
        values = variables()
        ns = yaml.safe_load(render((ROLE / "templates/etc/slurm/namespace.yaml").read_text(), values))
        self.assertEqual(
            ns,
            {
                "defaults": {
                    "auto_base_path": True,
                    "base_path": DEFAULTS["slurm_job_private_base"],
                    "dir_confs": [
                        {"path": d, "options": "nodev,nosuid"} for d in DEFAULTS["slurm_job_private_dirs"]
                    ],
                }
            },
        )
        self.assertNotIn(DEFAULTS["slurm_job_private_base"], DEFAULTS["slurm_job_private_dirs"])

    def test_off_leaves_no_plugin_and_contain_to_ssh(self):
        conf = settings(rendered("slurm_conf_template", slurm_job_private_dirs=[], slurm_contain_ssh=False))
        self.assertNotIn("NamespaceType", conf)
        self.assertNotIn("Contain", conf["PrologFlags"].split(","))

    def test_private_dirs_need_contain_even_without_ssh_containment(self):
        conf = settings(rendered("slurm_conf_template", slurm_contain_ssh=False))
        self.assertIn("Contain", conf["PrologFlags"].split(","))

    def test_the_controller_check_follows_the_setting(self):
        check = ControllerCheck()
        on = rendered("slurm_conf_template")
        off = rendered("slurm_conf_template", slurm_job_private_dirs=[])
        self.assertTrue(check.passes(on))
        self.assertTrue(check.passes(off, slurm_job_private_dirs=[]))
        self.assertFalse(check.passes(off), "the setting lists directories, the template names no plugin")
        self.assertFalse(check.passes(on, slurm_job_private_dirs=[]), "a plugin the setting does not ask for")
        self.assertFalse(check.passes(on.replace("namespace/linux", "namespace/tmpfs")))
        self.assertFalse(check.passes(on + "\nJobContainerType=job_container/tmpfs\n"))
        self.assertFalse(check.passes(on.replace("PrologFlags=Alloc,Serial,Contain", "PrologFlags=Alloc")))

    def test_settings_slurmd_would_refuse_are_refused(self):
        check = task("roles/slurm/tasks/controller.yml", "Check the per-job private directories")

        def passes(dirs, base="/var/spool/slurmd-private"):
            values = variables(slurm_job_private_dirs=dirs, slurm_job_private_base=base)
            values.update(
                {k: render(v, values) if isinstance(v, str) else v for k, v in check["vars"].items()}
            )
            return condition(check["assert"]["that"], values)

        self.assertTrue(passes(DEFAULTS["slurm_job_private_dirs"], DEFAULTS["slurm_job_private_base"]))
        self.assertTrue(passes(["/tmp"], "/tmpdata"), "a sibling sharing a prefix is not under it")
        for dirs, base in (
            (["/tmp", "/tmp"], "/var/spool/x"),
            (["tmp"], "/var/spool/x"),
            (["/tmp/"], "/var/spool/x"),
            (["/tmp,/dev/shm"], "/var/spool/x"),
            (["/tmp"], "/tmp"),
            (["/tmp"], "/tmp/base"),
            (["/var/tmp"], "relative"),
        ):
            with self.subTest(dirs=dirs, base=base):
                self.assertFalse(passes(dirs, base))

    def test_the_controller_serves_or_removes_the_file(self):
        put = task("roles/slurm/tasks/controller.yml", "Configure namespace.yaml")
        gone = task("roles/slurm/tasks/controller.yml", "Remove namespace.yaml")
        self.assertTrue(condition(put["when"], variables()))
        self.assertFalse(condition(gone["when"], variables()))
        self.assertTrue(condition(gone["when"], variables(slurm_job_private_dirs=[])))
        self.assertEqual(put["notify"], gone["notify"])


class ClusterNameGuard(unittest.TestCase):
    """The controller refuses a ClusterName its saved state does not belong to."""

    GUARD = task("roles/slurm/tasks/controller.yml", "Refuse a ClusterName change")

    def passes(self, saved, name):
        values = variables(slurm_cluster_name=name)
        values.update(self.GUARD["vars"])
        values["slurm_state_clustername"] = {"content": base64.b64encode(saved.encode()).decode()}
        return condition(self.GUARD["assert"]["that"], values)

    def test_same_name_passes(self):
        self.assertTrue(self.passes("deepops|1234", "deepops"))
        self.assertTrue(self.passes("deepops", "deepops"))

    def test_new_name_is_refused(self):
        self.assertFalse(self.passes("deepops|1234", "renamed"))


class ConfServer(unittest.TestCase):
    def test_slurmd_and_sackd_ask_the_controllers(self):
        self.assertEqual(render(DEFAULTS["slurm_conf_servers"], variables()), "ctl01:6817")
        self.assertEqual(
            render(DEFAULTS["slurm_conf_servers"], variables(slurm_enable_ha=True)), "ctl01:6817,ctl02:6817"
        )

    def test_slurmd_options_carry_the_conf_server(self):
        block = task("roles/slurm/tasks/compute.yml", "Configure slurmd for configless and pmix")[
            "blockinfile"
        ]["block"]
        self.assertIn('SLURMD_OPTIONS="--conf-server ctl01:6817"', render(block, variables()))

    def test_compute_nodes_keep_no_copy(self):
        removed = task(
            "roles/slurm/tasks/compute.yml", "Remove local copies of the configuration slurmctld serves"
        )
        self.assertEqual(removed["loop"], ["slurm.conf", "cgroup.conf", "gres.conf", "namespace.yaml"])
        self.assertEqual(removed["file"]["state"], "absent")


if __name__ == "__main__":
    unittest.main()
