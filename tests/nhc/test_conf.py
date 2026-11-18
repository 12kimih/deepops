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
from ansible_yaml import render  # noqa: E402

ROLE = ROOT / "roles" / "nhc"
FACTS = {"hostname": "node1", "memtotal_mb": 1000, "interfaces": ["lo", "eth0"],
         "local": {"gpus": {"count": 0},
                   "topology": {"cpu_topology": {"sockets": 2, "physical_cores": 8, "logical_cpus": 16}}}}


def conf(varfile):
    variables = {**(yaml.safe_load((ROLE / "defaults" / "main.yml").read_text()) or {}),
                 **yaml.safe_load((ROLE / "vars" / "main.yml").read_text()),
                 **yaml.safe_load((ROLE / "vars" / varfile).read_text()),
                 "ansible_managed": "", "ansible_facts": FACTS}
    return render((ROLE / "templates" / "nhc.conf.j2").read_text(), variables)


class NhcConf(unittest.TestCase):
    def test_services_are_checked_through_systemd(self):
        for varfile, ssh in (("debian.yml", "ssh.service ssh.socket"), ("redhat.yml", "sshd.service sshd.socket")):
            with self.subTest(family=varfile):
                text = conf(varfile)
                self.assertNotIn("check_ps_service", text)
                self.assertIn(f"systemctl is-active --quiet {ssh}", text)
                self.assertIn("systemctl is-active --quiet slurmd.service", text)


if __name__ == "__main__":
    unittest.main()
