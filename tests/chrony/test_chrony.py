"""chrony.conf as roles/deepops_chrony renders it: upstream everywhere by default, or
the cluster's own time servers serving the rest."""

import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from ansible_yaml import ROOT, render, role_defaults  # noqa: E402

ROLE = ROOT / "roles/deepops_chrony"
TEMPLATE = (ROLE / "templates/chrony.conf.j2").read_text()
FACTS = {
    "master": {"default_ipv4": {"address": "192.0.2.1"}, "all_ipv4_addresses": ["192.0.2.1", "10.0.0.1"]},
    "n1": {"default_ipv4": {"address": "192.0.2.11"}, "all_ipv4_addresses": ["192.0.2.11", "10.0.0.11"]},
}


def conf(host="n1", os="debian", **settings):
    values = {
        **role_defaults("deepops_chrony"),
        **yaml.safe_load((ROLE / "vars/main.yml").read_text()),
        **yaml.safe_load((ROLE / f"vars/{os}.yml").read_text()),
        "cluster_networks": ["10.0.0.0/16"],
        **settings,
        "inventory_hostname": host,
        "hostvars": {h: {"ansible_facts": f} for h, f in FACTS.items()},
        "ansible_facts": {"os_family": "Debian" if os == "debian" else "RedHat"},
        "ansible_managed": "Ansible managed",
    }
    return [line for line in render(TEMPLATE, values).splitlines() if line and not line.startswith("#")]


def directives(lines, name):
    return [line.split(None, 1)[1] for line in lines if line.split()[0] == name]


class Upstream(unittest.TestCase):
    def test_every_host_takes_upstream_and_serves_none(self):
        lines = conf()
        self.assertEqual(
            directives(lines, "pool"), [f"{n}.pool.ntp.org iburst maxsources 2" for n in range(3)]
        )
        self.assertEqual(directives(lines, "allow") + directives(lines, "local"), [])

    def test_the_distributions_directories_and_leap_seconds_are_kept(self):
        lines = conf()
        self.assertEqual(directives(lines, "confdir"), ["/etc/chrony/conf.d"])
        self.assertEqual(directives(lines, "sourcedir"), ["/etc/chrony/sources.d"])
        self.assertEqual(directives(lines, "leapsectz"), ["right/UTC"])
        self.assertEqual(directives(conf(os="redhat"), "confdir"), [], "RHEL's chrony.conf has none")

    def test_no_directive_chrony_dropped(self):
        # chrony 4 warns about each and ignores it; they only hid real warnings.
        for gone in ("stratumweight", "commandkey", "generatecommandkey"):
            self.assertEqual(directives(conf(), gone), [], gone)


class Serving(unittest.TestCase):
    SERVED = {"chrony_serve_hosts": ["master"]}

    def test_the_server_takes_upstream_and_answers_the_cluster(self):
        lines = conf("master", **self.SERVED)
        self.assertEqual(len(directives(lines, "pool")), 3)
        self.assertEqual(directives(lines, "allow"), ["10.0.0.0/16"])
        self.assertEqual(directives(lines, "local"), ["stratum 10 orphan"])

    def test_the_rest_take_time_from_it_inside_the_cluster(self):
        lines = conf("n1", **self.SERVED)
        self.assertEqual(directives(lines, "server"), ["10.0.0.1 iburst"])
        self.assertEqual(directives(lines, "pool") + directives(lines, "allow"), [])

    def test_a_server_without_facts_is_named(self):
        lines = conf("n1", chrony_serve_hosts=["master", "ntp1"])
        self.assertEqual(directives(lines, "server"), ["10.0.0.1 iburst", "ntp1 iburst"])


if __name__ == "__main__":
    unittest.main()
