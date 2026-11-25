#!/usr/bin/env python3
"""roles/slurm's config files from settings (plugins/filter/slurm_conf.py): every key
checked against Slurm's parameters, the site's settings over deepops' defaults, the
role's own values fixed, and a site's own files compared setting by setting.

The equivalence case is the one a site meets moving from its own files to the role's:
fixtures/ holds a site's slurm.conf, cgroup.conf, gres.conf and slurmdbd.conf, and
fixtures/settings.yml the settings that say the same; the role's templates rendered with
them must differ in nothing (roles/slurm/tasks/conf-compare.yml runs the same check on
a real site). Run with Ansible's Python; no host is contacted.
"""

import importlib.util
import os
import re
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from ansible_yaml import load, render, role_variables, same_name  # noqa: E402

ROOT = Path(os.environ.get("DEEPOPS_TEST_ROOT", Path(__file__).resolve().parents[2]))
ROLE = ROOT / "roles/slurm"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
spec = importlib.util.spec_from_file_location("slurm_conf", ROOT / "plugins/filter/slurm_conf.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)
FILES = ("slurm.conf", "cgroup.conf", "gres.conf", "slurmdbd.conf")


def facts(cpus=8, sockets=1, cores=4, threads=2, mb=2048, gpus=2):
    return {
        "ansible_local": {
            "memory": {"total_mb": mb},
            "topology": {
                "cpu_topology": {
                    "logical_cpus": str(cpus),
                    "sockets": str(sockets),
                    "cores_per_socket": str(cores),
                    "threads_per_core": str(threads),
                },
                "gpu_topology": ["0-3"] * gpus,
            },
        }
    }


def variables(site=None, hosts=None, **overrides):
    """The role's variables with SITE (group_vars) over them; HOSTS each node's host_vars,
    as hostvars[node] reads them (group_vars under host_vars)."""
    site = site or {}
    nodes = hosts or {"node01": {}}
    values = {
        **role_variables("slurm"),
        **site,
        "groups": {"slurm-master": ["ctl01"], "slurm-node": list(nodes)},
        "slurm_db_password": "@slurm_db_password@",
        "slurm_job_private_dirs": [],
        **overrides,
    }
    values["hostvars"] = {n: {**values, **h, "ansible_facts": facts()} for n, h in nodes.items()}
    return values


def rendered(conf, values):
    return render((ROLE / "templates/etc/slurm" / conf).read_text(), values)


def lines(text):
    return [x for x in text.splitlines() if x and not x.startswith("#")]


class Settings(unittest.TestCase):
    def test_a_later_layer_wins_and_null_leaves_a_key_out(self):
        out = sc.slurm_settings(
            [{"KillWait": 30, "MinJobAge": 300}, {"KillWait": 120, "MinJobAge": None}], "slurm.conf"
        )
        self.assertEqual(out, {"KillWait": 120})

    def test_keys_are_slurms_in_any_case_spelt_as_slurm_spells_them(self):
        self.assertEqual(list(sc.slurm_settings([{"waittime": 0}], "slurm.conf")), ["WaitTime"])
        self.assertEqual(
            list(sc.slurm_settings([{"DefCpuPerGPU": 4}], "slurm.conf", "partition")), ["DefCPUPerGPU"]
        )

    def test_a_misspelt_misplaced_or_defunct_key_is_refused(self):
        for layer, part, why in (
            ({"KilWait": 30}, "global", "not a global parameter"),
            ({"Weight": 1}, "global", "not a global parameter"),  # a node's
            ({"AccountingStorageUser": "slurm"}, "global", "defunct"),
        ):
            with self.subTest(layer=layer), self.assertRaisesRegex(sc.Error, why):
                sc.slurm_settings([layer], "slurm.conf", part)
        with self.assertRaisesRegex(sc.Error, "defunct"):
            sc.slurm_settings([{"CgroupAutomount": True}], "cgroup.conf")

    def test_the_roles_own_values_cannot_be_overridden(self):
        with self.assertRaisesRegex(sc.Error, "TmpFS is set by roles/slurm"):
            sc.slurm_settings([{"tmpfs": "/tmp"}], "slurm.conf", fixed={"TmpFS": "/x"})
        out = sc.slurm_settings([{"KillWait": 1}], "slurm.conf", fixed={"TmpFS": "", "ClusterName": "c"})
        self.assertEqual(out, {"ClusterName": "c", "KillWait": 1}, "an empty fixed value leaves the key out")

    def test_lines_in_order_lists_flags_quotes_and_repeated_keys(self):
        settings = sc.slurm_settings(
            [{"SlurmctldHost": ["a", "b"], "SelectTypeParameters": ["x", "y"], "RebootProgram": "/bin/systemctl reboot"},
             {"AllowSpecResourcesUsage": False}],
            "slurm.conf",
        )  # fmt: skip
        self.assertEqual(
            sc.slurm_lines(settings, "slurm.conf"),
            ["AllowSpecResourcesUsage=NO", 'RebootProgram="/bin/systemctl reboot"',
             "SelectTypeParameters=x,y", "SlurmctldHost=a", "SlurmctldHost=b"],
        )  # fmt: skip

    def test_a_record_starts_with_its_name(self):
        node = sc.slurm_settings([{"Weight": 1, "NodeName": "n1", "CPUs": 2}], "slurm.conf", "node")
        self.assertEqual(sc.slurm_record(node, "slurm.conf", "node"), "NodeName=n1 CPUs=2 Weight=1")
        with self.assertRaisesRegex(sc.Error, "needs PartitionName"):
            sc.slurm_record({"Nodes": "ALL"}, "slurm.conf", "partition")


class Hardware(unittest.TestCase):
    def test_as_slurmd_reports_it_memory_in_whole_gib(self):
        host = {"ansible_facts": facts(cpus=96, sockets=2, cores=48, threads=1, mb=515_900, gpus=8)}
        self.assertEqual(
            sc.slurm_node_hardware(host, True, 4096),
            {"CPUs": 96, "Boards": 1, "SocketsPerBoard": 2, "CoresPerSocket": 48, "ThreadsPerCore": 1,
             "RealMemory": 515_072, "Gres": "gpu:8", "TmpDisk": 4096},
        )  # fmt: skip

    def test_gres_counts_the_nodes_gres_conf_lines_by_type(self):
        host = {
            "ansible_facts": facts(),
            "slurm_gres": [
                {"Name": "gpu", "Type": "a", "File": "/dev/nvidia[0-2]"},
                {"Name": "gpu", "Type": "a", "File": "/dev/nvidia3"},
                {"Name": "gpu", "Type": "b", "Count": 2},
            ],
        }
        self.assertEqual(sc.slurm_node_hardware(host)["Gres"], ["gpu:a:4", "gpu:b:2"])
        self.assertNotIn("Gres", sc.slurm_node_hardware(host, manage_gpus=False))


class Diff(unittest.TestCase):
    def test_the_same_settings_however_written(self):
        old = 'KillWait=120\nwaittime=0\nRebootProgram="/bin/systemctl reboot"  # x\nPartitionName=p Nodes=n[1-2] Default=yes\n'
        new = 'WaitTime = 0\nKillWait=120\nRebootProgram="/bin/systemctl reboot"\nPartitionName=p Default=YES Nodes=n1,n2\n'
        self.assertEqual(sc.slurm_conf_diff(old, new, "slurm.conf"), [])

    def test_every_global_node_partition_and_gres_difference(self):
        old = "MinJobAge=120\nNodeName=n1 CPUs=8 Weight=1\nPartitionName=p Nodes=n1 MaxTime=1-0\n"
        new = "NodeName=n1 CPUs=8 Weight=2\nPartitionName=p Nodes=n1\nPartitionName=q Nodes=n1\n"
        self.assertEqual(
            [sc.slurm_conf_difference_line(d) for d in sc.slurm_conf_diff(old, new, "slurm.conf")],
            ["slurm.conf: MinJobAge: 120 -> (unset)", "node n1: Weight: 1 -> 2",
             "partition p: MaxTime: 1-0 -> (unset)", "partition q: Nodes: (unset) -> n1",
             "partition q: PartitionName: (unset) -> q"],
        )  # fmt: skip
        gres = sc.slurm_conf_diff(
            "NodeName=n1 Name=gpu Type=a File=/dev/nvidia0 Cores=0-3\n",
            "NodeName=n1 Name=gpu Type=a File=/dev/nvidia0 Cores=0-7\n",
            "gres.conf",
        )
        self.assertEqual([(d["key"], d["old"], d["new"]) for d in gres], [("Cores", "0-3", "0-7")])


class Equivalence(unittest.TestCase):
    """fixtures/: a site's own files, and the settings that say the same."""

    @classmethod
    def setUpClass(cls):
        cls.settings = yaml.safe_load((FIXTURES / "settings.yml").read_text())

    def test_the_roles_rendering_of_the_settings_says_what_the_sites_files_say(self):
        values = variables(self.settings["group_vars"], self.settings["hosts"])
        for conf in FILES:
            with self.subTest(conf=conf):
                diff = sc.slurm_conf_diff((FIXTURES / conf).read_text(), rendered(conf, values), conf)
                self.assertEqual([sc.slurm_conf_difference_line(d) for d in diff], [])

    def test_a_setting_left_out_is_a_difference(self):
        own = {k: v for k, v in self.settings["group_vars"]["slurm_conf"].items() if k != "MinJobAge"}
        site = {**self.settings["group_vars"], "slurm_conf": own}
        values = variables(site, self.settings["hosts"])
        diff = sc.slurm_conf_diff(
            (FIXTURES / "slurm.conf").read_text(), rendered("slurm.conf", values), "slurm.conf"
        )
        self.assertEqual([(d["key"], d["old"], d["new"]) for d in diff], [("MinJobAge", "120", "300")])


class Role(unittest.TestCase):
    def test_every_default_is_a_slurm_key_and_none_is_the_roles(self):
        v = role_variables("slurm")
        for conf, defaults, computed in (
            ("slurm.conf", "slurm_conf_defaults", "_slurm_conf_computed"),
            ("cgroup.conf", "slurm_cgroup_conf_defaults", "_slurm_cgroup_conf_computed"),
            ("gres.conf", "slurm_gres_conf_defaults", "_slurm_gres_conf_computed"),
            ("slurmdbd.conf", "slurm_dbd_conf_defaults", "_slurm_dbd_conf_computed"),
        ):
            with self.subTest(conf=conf):
                sc.slurm_settings([v[defaults]], conf)
                sc.slurm_settings([], conf, fixed=v[computed])
                self.assertFalse({k.lower() for k in v[defaults]} & {k.lower() for k in v[computed]})

    def test_the_default_partition_and_node(self):
        conf = lines(rendered("slurm.conf", variables()))
        self.assertIn(
            "NodeName=node01 Boards=1 CoresPerSocket=4 CPUs=8 Gres=gpu:2 RealMemory=2048 SocketsPerBoard=1 ThreadsPerCore=2",
            conf,
        )
        self.assertIn(
            "PartitionName=batch Default=YES DefMemPerCPU=0 MaxTime=INFINITE Nodes=ALL OverSubscribe=NO State=UP",
            conf,
        )

    def test_policy_then_host_over_what_the_role_measures(self):
        hosts = {
            "node01": {
                "slurm_node_policy": {"RealMemory": 1000, "Weight": 5},
                "slurm_node_conf": {"Weight": 7, "Gres": None},
            }
        }
        node = next(
            x for x in lines(rendered("slurm.conf", variables(hosts=hosts))) if x.startswith("NodeName=")
        )
        self.assertIn("RealMemory=1000", node)
        self.assertIn("Weight=7", node)
        self.assertNotIn("Gres=", node)

    def test_a_sites_typo_fails_the_rendering(self):
        with self.assertRaisesRegex(Exception, "KilWait is not a global parameter"):
            rendered("slurm.conf", variables({"slurm_conf": {"KilWait": 30}}))

    def test_tres_lists_each_gpu_type_of_the_nodes_gres_lines(self):
        hosts = {"a": {"slurm_gres": [{"Name": "gpu", "Type": "y", "File": "/dev/nvidia0"}]},
                 "b": {"slurm_gres": [{"Name": "gpu", "Type": "x", "File": "/dev/nvidia0"}]}}  # fmt: skip
        conf = lines(rendered("slurm.conf", variables(hosts=hosts)))
        self.assertIn("AccountingStorageTRES=gres/gpu,gres/gpu:x,gres/gpu:y", conf)

    def test_the_compare_task_renders_both_with_the_password_a_placeholder(self):
        task = next(
            t
            for t in load("roles/slurm/tasks/conf-compare.yml")
            if same_name(t.get("name"), "Compare each site file with the role's rendering")
        )
        self.assertEqual(task["vars"]["slurm_db_password"], "@slurm_db_password@")
        self.assertEqual(sorted(i["conf"] for i in task["loop"]), sorted(FILES))


class Parameters(unittest.TestCase):
    def test_pinned_for_the_slurm_the_role_installs(self):
        self.assertEqual(sc.PARAMETERS["version"], role_variables("slurm")["slurm_version"])

    def test_the_generator_reads_slurms_tables(self):
        spec = importlib.util.spec_from_file_location(
            "slurm_parameters", ROOT / "scripts/deepops/slurm_parameters.py"
        )
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        text = 's_p_options_t slurm_conf_options[] = {\n\t{"KillWait", S_P_UINT16},\n\t{"Old", S_P_STRING, _defunct_option},\n\t{.key = "NodeName", .type = S_P_ARRAY, .handler = _parse},\n\t{NULL}\n};'
        self.assertEqual(
            gen.table(text, "s_p_options_t slurm_conf_options[] = {"),
            {"KillWait": {"type": "uint16", "defunct": False}, "Old": {"type": "string", "defunct": True},
             "NodeName": {"type": "array", "defunct": False}},
        )  # fmt: skip
        self.assertTrue(re.match(r"\d+\.\d+\.\d+$", sc.PARAMETERS["version"]))


if __name__ == "__main__":
    unittest.main()
