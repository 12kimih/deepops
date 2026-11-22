"""NHC checks services through systemd, with the unit names of each OS family.

A process check missed a socket-activated sshd (no process until the first
login) and a colonless daemon name failed on every Ubuntu node, so NHC drained
healthy idle nodes. Asking systemd also fails a node whose systemd hangs.
"""

from pathlib import Path
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import render, role_defaults  # noqa: E402

ROLE = ROOT / "roles" / "nhc"
FACTS = {
    "hostname": "node1",
    "memtotal_mb": 1000,
    "interfaces": ["lo", "eth0"],
    "mounts": [{"mount": "/"}, {"mount": "/boot"}, {"mount": "/var/spool"}],
    "ansible_local": {
        "gpus": {"count": 0},
        "topology": {"cpu_topology": {"sockets": 2, "physical_cores": 8, "logical_cpus": 16}},
    },
}


def conf(varfile, family, gpus=0, record=None, **overrides):
    """nhc.conf for a host of FAMILY with GPUS, whose component record (roles/component_record)
    holds RECORD ({component: state})."""
    local = {**FACTS["ansible_local"], "gpus": {"count": gpus}}
    if record is not None:
        local["deepops_components"] = {
            "components": {k: {"state": v, "version": ""} for k, v in record.items()}
        }
    variables = {
        **role_defaults("nhc"),
        **yaml.safe_load((ROLE / "vars" / varfile).read_text()),
        "ansible_managed": "",
        "ansible_facts": {**FACTS, "os_family": family, "ansible_local": local},
        **overrides,
    }
    return render((ROLE / "templates" / "nhc.conf.j2").read_text(), variables)


class NhcConf(unittest.TestCase):
    def test_services_are_checked_through_systemd(self):
        for varfile, family, ssh in (
            ("debian.yml", "Debian", "ssh.service ssh.socket"),
            ("redhat.yml", "RedHat", "sshd.service sshd.socket"),
        ):
            with self.subTest(family=family):
                text = conf(varfile, family)
                self.assertNotIn("check_ps_service", text)
                self.assertIn(f"systemctl is-active --quiet {ssh}", text)
                self.assertIn("systemctl is-active --quiet slurmd.service", text)

    def test_gpu_faults_drain_gpu_nodes_only(self):
        xids = " ".join(str(x) for x in role_defaults("nhc")["nhc_gpu_drain_xids"])
        gpu = conf("debian.yml", "Debian", gpus=8)
        self.assertIn(f"check_nv_xid {xids}\n", gpu)
        self.assertIn("check_nv_remapped_rows", gpu)
        cpu = conf("debian.yml", "Debian")
        self.assertNotIn("check_nv_", cpu)

    def test_the_file_does_not_follow_the_interfaces_up_now(self):
        """Docker's bridges and veths come and go with containers: listing what is up now
        changed nhc.conf on every run. Only nhc_check_interfaces is checked."""
        churn = {**FACTS, "interfaces": ["lo", "eth0", "br-1a2b", "veth9c8d", "docker0"]}
        self.assertEqual(
            conf("debian.yml", "Debian"),
            conf("debian.yml", "Debian", ansible_facts={**churn, "os_family": "Debian"}),
        )
        self.assertNotIn("check_hw_eth", conf("debian.yml", "Debian"))
        text = conf("debian.yml", "Debian", nhc_check_interfaces=["eth0", "ib0"])
        self.assertIn(" node1 || check_hw_eth eth0\n node1 || check_hw_eth ib0\n", text)

    def test_each_gpu_check_turns_off(self):
        text = conf("debian.yml", "Debian", gpus=8, nhc_gpu_drain_xids=[], nhc_gpu_check_remapped_rows=False)
        self.assertNotIn("check_nv_xid", text)
        self.assertNotIn("check_nv_remapped_rows", text)

    def test_acs_is_checked_where_its_component_is_installed(self):
        """The record says what a host has; the configured state only what it should get.
        Configured present before roles/pcie_acs had run, the check ran a program no node
        had yet and would have drained them all."""
        program = role_defaults("deepops_paths")["pcie_acs_program"]
        for state, checked in (("present", True), ("begin", False), ("absent", False)):
            with self.subTest(recorded=state):
                text = conf("debian.yml", "Debian", record={"pcie_acs": state}, pcie_acs_program=program)
                self.assertEqual(f"check_pcie_acs {program}\n" in text, checked)
        configured_only = conf(
            "debian.yml",
            "Debian",
            record={},
            deepops_components={"pcie_acs": "present"},
            pcie_acs_program=program,
        )
        self.assertNotIn("check_pcie_acs", configured_only)
        self.assertNotIn("check_pcie_acs", conf("debian.yml", "Debian"))

    def test_a_full_local_filesystem_drains_the_node(self):
        """Each path is named by the mount it lives on (NHC's check matches mount
        points only and passes on any other directory), once per filesystem."""
        on = conf("debian.yml", "Debian")
        free = [
            line.split("|| ")[1]
            for line in on.splitlines()
            if "check_fs_free" in line or "check_fs_ifree" in line
        ]
        self.assertEqual(
            free,
            [
                "check_fs_free / 5%",
                "check_fs_ifree / 5%",
                "check_fs_free /var/spool 5%",
                "check_fs_ifree /var/spool 5%",
            ],
        )
        off = conf("debian.yml", "Debian", slurm_job_private_dirs=[])
        self.assertIn("check_fs_free / 5%", off)
        self.assertNotIn("/var/spool", off)
        same = conf("debian.yml", "Debian", nhc_check_free_paths=["/", "/srv/a", "/srv/b"])
        self.assertEqual(same.count("check_fs_free"), 1)


if __name__ == "__main__":
    unittest.main()
