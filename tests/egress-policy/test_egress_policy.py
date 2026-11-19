"""roles/egress_policy: its address filters and what its templates render.

The ruleset itself is checked by `nft -c` when the role writes it (template validate);
here the rendering is checked without a kernel: the internal networks split by address
family, the inbound chain only when asked for, and the SSH multiplexing only where
~/.ssh is writable (ssh exits when it cannot create the control socket).
"""

import importlib.util
import json
import re
import unittest
from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles" / "egress_policy"
spec = importlib.util.spec_from_file_location("networks", ROOT / "plugins" / "filter" / "networks.py")
networks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(networks)
DEFAULTS = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
VARS = yaml.safe_load((ROLE / "vars" / "main.yml").read_text())


def render(name, **overrides):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROLE / "templates"), trim_blocks=True)
    env.filters.update(networks.FilterModule().filters())
    env.filters["to_json"] = json.dumps
    env.filters["comment"] = lambda text: "# " + text
    values = {**DEFAULTS, **VARS, "ansible_managed": "managed", "node_exporter_prom_dir": "/run/prometheus"}
    values.update(egress_policy_internal_networks=["192.0.2.0/24", "198.51.100.7", "2001:db8::/32"])
    values.update(overrides)
    return env.get_template(name).render(**values)


class Filters(unittest.TestCase):
    def test_networks(self):
        nets = ["192.0.2.0/24", "198.51.100.7", "2001:db8::1/32", "not-a-net", "10.0.0.0/33"]
        self.assertEqual(networks.networks_invalid(nets), ["not-a-net", "10.0.0.0/33"])
        self.assertEqual(networks.networks_of_version(nets, 4), ["192.0.2.0/24", "198.51.100.7/32"])
        self.assertEqual(networks.networks_of_version(nets, 6), ["2001:db8::/32"])
        self.assertTrue(networks.ip_in_networks("192.0.2.9", nets))
        self.assertFalse(networks.ip_in_networks("203.0.113.1", nets))
        self.assertFalse(networks.ip_in_networks("garbage", nets))

    def test_ports(self):
        self.assertTrue(networks.port_in_ports("22", [22]))
        self.assertTrue(networks.port_in_ports(60005, [22, "60000-60010"]))
        self.assertFalse(networks.port_in_ports(2222, [22, "60000-60010"]))


class Ruleset(unittest.TestCase):
    def test_internal_networks_by_family_and_exempt_uids_sorted(self):
        text = render("egress-policy.nft.j2", egress_policy_exempt_uids=[1003, "1001", 1003])
        self.assertIn("elements = { 192.0.2.0/24, 198.51.100.7/32 };", text)
        self.assertIn("elements = { 2001:db8::/32 };", text)
        self.assertIn("set exempt_uids { typeof meta skuid; elements = { 1001, 1003 }; }", text)
        self.assertIn("meta skuid { 1000-60000, 65536-4294967294 } jump user", text)

    def test_ssh_limits_come_from_the_defaults(self):
        text = render("egress-policy.nft.j2")
        self.assertIn("limit rate over 20/minute burst 30 packets", text)
        self.assertIn("limit rate over 60/minute burst 120 packets jump ssh_node_reject", text)
        self.assertIn("tcp dport { 53, 80, 443 } accept", text)
        self.assertIn("udp dport { 53, 123 } accept", text)

    def test_inbound_chain_only_when_asked(self):
        self.assertNotIn("hook input", render("egress-policy.nft.j2"))
        text = render(
            "egress-policy.nft.j2",
            egress_policy_inbound=True,
            egress_policy_inbound_tcp_ports=[22, "60000-60010"],
            egress_policy_trusted_interfaces=["wg0"],
        )
        self.assertIn("policy drop;", text)
        self.assertIn("elements = { 22, 60000-60010 };", text)
        self.assertIn('iifname { "wg0" } accept', text)

    def test_only_this_table_is_replaced(self):
        text = render("egress-policy.nft.j2")
        self.assertNotRegex(text, r"(?m)^\s*flush ruleset")
        self.assertEqual(
            re.findall(r"(?m)^(?:delete )?table \S+ \S+", text),
            ["table inet egress_policy", "delete table inet egress_policy", "table inet egress_policy"],
        )


class SshClient(unittest.TestCase):
    def test_multiplexing_only_where_the_socket_can_be_made(self):
        text = render("ssh_config.conf.j2")
        self.assertIn("    ConnectionAttempts 1\n", text)
        self.assertIn('Match exec "test -w %d/.ssh"\n    ControlMaster auto\n', text)
        self.assertNotIn("Match", render("ssh_config.conf.j2", egress_policy_ssh_multiplex_options={}))


if __name__ == "__main__":
    unittest.main()
