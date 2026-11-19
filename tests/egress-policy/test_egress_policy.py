"""roles/egress_policy and roles/dns_resolver: their filters and what they render.

The ruleset is checked by `nft -c` and the resolver configuration by
`unbound-checkconf` when the roles write them (template validate); here the rendering
is checked without a kernel: the internal networks split by address family, users'
DNS kept to the local resolver, the node-wide SSH bucket by host role, the inbound
chain only when asked for, SSH multiplexing only where ~/.ssh is writable, and the
resolver's blocked names. Container publishes in the roles must bind an address, or
Docker forwards them past the input filter.
"""

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

import jinja2
import jinja2.nativetypes
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

ROLE = ROOT / "roles" / "egress_policy"
RESOLVER = ROOT / "roles" / "dns_resolver"
spec = importlib.util.spec_from_file_location("networks", ROOT / "plugins" / "filter" / "networks.py")
networks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(networks)


def load(role):
    return {
        **yaml.safe_load((role / "defaults" / "main.yml").read_text()),
        **yaml.safe_load((role / "vars" / "main.yml").read_text()),
    }


def render(role, name, host="n1", groups=None, **overrides):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(role / "templates"), trim_blocks=True)
    env.filters.update(networks.FilterModule().filters())
    env.filters["to_json"] = json.dumps
    env.filters["comment"] = lambda text: "# " + text
    env.filters["regex_replace"] = lambda text, pattern, repl="": re.sub(pattern, repl, text)
    values = {**load(role), "ansible_managed": "managed", "node_exporter_prom_dir": "/run/prometheus"}
    values.update(inventory_hostname=host, groups=groups or {"slurm-login": ["login1"]})
    values.update(egress_policy_internal_networks=["192.0.2.0/24", "198.51.100.7", "2001:db8::/32"])
    values.update(overrides)
    # Role defaults that refer to other values, typed as Ansible types them (a list stays
    # a list).
    native = jinja2.nativetypes.NativeEnvironment()
    native.filters = env.filters
    for key, value in values.items():
        if isinstance(value, str) and "{{" in value:
            values[key] = native.from_string(value).render(**values)
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
    def nft(self, **overrides):
        return render(ROLE, "egress-policy.nft.j2", **overrides)

    def test_internal_networks_by_family_and_exempt_uids_sorted(self):
        text = self.nft(egress_policy_exempt_uids=[1003, "1001", 1003])
        self.assertIn("elements = { 192.0.2.0/24, 198.51.100.7/32 };", text)
        self.assertIn("elements = { 2001:db8::/32 };", text)
        self.assertIn("set exempt_uids { typeof meta skuid; elements = { 1001, 1003 }; }", text)
        self.assertIn("meta skuid { 1000-60000, 65536-4294967294 } jump user", text)

    def test_web_ntp_and_ping_but_not_quic(self):
        text = self.nft()
        self.assertIn("tcp dport { 80, 443 } accept", text)
        self.assertIn("udp dport { 123 } accept", text)
        self.assertIn("icmp type echo-request accept", text)
        self.assertNotIn("icmp type echo-request", self.nft(egress_policy_allow_ping=False))

    def test_dns_only_to_the_local_resolver_even_internal_ones(self):
        text = self.nft()
        dns = text.index("th dport { 53, 853 } jump dns_reject")
        self.assertLess(dns, text.index("ip daddr @internal4 accept"))
        self.assertNotIn("jump dns_reject", self.nft(egress_policy_dns_local_only=False))

    def test_the_node_ssh_bucket_is_generous_on_login_nodes(self):
        self.assertIn("limit rate over 20/minute burst 30 packets", self.nft())
        self.assertIn("limit rate over 60/minute burst 120 packets jump ssh_node_reject", self.nft(host="n1"))
        self.assertIn(
            "limit rate over 600/minute burst 1200 packets jump ssh_node_reject", self.nft(host="login1")
        )

    def test_inbound_chain_only_when_asked(self):
        self.assertNotIn("hook input", self.nft())
        text = self.nft(
            egress_policy_inbound=True,
            egress_policy_inbound_tcp_ports=[22, "60000-60010"],
            egress_policy_trusted_interfaces=["wg0"],
        )
        self.assertIn("policy drop;", text)
        self.assertIn("elements = { 22, 60000-60010 };", text)
        self.assertIn('iifname { "wg0" } accept', text)

    def test_only_this_table_is_replaced(self):
        text = self.nft()
        self.assertNotRegex(text, r"(?m)^\s*flush ruleset")
        self.assertEqual(
            re.findall(r"(?m)^(?:delete )?table \S+ \S+", text),
            ["table inet egress_policy", "delete table inet egress_policy", "table inet egress_policy"],
        )


class SshClient(unittest.TestCase):
    def test_multiplexing_only_where_the_socket_can_be_made(self):
        text = render(ROLE, "ssh_config.conf.j2")
        self.assertIn("    ConnectionAttempts 1\n", text)
        self.assertIn('Match exec "test -w %d/.ssh"\n    ControlMaster auto\n', text)
        self.assertNotIn("Match", render(ROLE, "ssh_config.conf.j2", egress_policy_ssh_multiplex_options={}))


class Resolver(unittest.TestCase):
    def test_loopback_only_blocked_names_and_upstreams(self):
        text = render(
            RESOLVER,
            "unbound.conf.j2",
            dns_resolver_upstreams=["192.0.2.53", "2001:db8::53"],
            dns_resolver_blocked_domains_site=["Tunnel.Example.NET", "ngrok.io"],
        )
        self.assertEqual(re.findall(r"(?m)^\s+interface: (\S+)", text), ["127.0.0.1"])
        zones = re.findall(r'local-zone: "(\S+)" always_nxdomain', text)
        self.assertIn("ngrok.io.", zones)
        self.assertIn("tunnel.example.net.", zones)
        self.assertEqual(len(zones), len(set(zones)))
        self.assertIn("forward-addr: 192.0.2.53\n    forward-addr: 2001:db8::53", text)
        self.assertNotIn("module-config", text)

    def test_tailscale_stays_reachable(self):
        # The administrators' tailscaled needs its control plane.
        domains = load(RESOLVER)["dns_resolver_blocked_domains"]
        self.assertFalse([d for d in domains if "tailscale" in d or d.endswith("ts.net")])

    def test_no_upstreams_means_unbound_recurses_itself(self):
        text = render(RESOLVER, "unbound.conf.j2", dns_resolver_dnssec=False)
        self.assertNotIn("forward-zone", text)
        self.assertIn('module-config: "iterator"', text)


class ContainerPublishes(unittest.TestCase):
    # -p 9400 or published_ports: ["9400:9400"] binds every address, and Docker's DNAT
    # forwards it to the container before any input filter sees it.
    PUBLISH = re.compile(r"""(?:\s(?:-p|--publish)[ =]|published_ports:\s*\[?\s*-?\s*)["']?([^\s"',\]]+)""")
    BOUND = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}|\[[0-9a-fA-F:]+\]|\{\{[^}]*\}\}):")

    def test_every_publish_binds_an_address(self):
        offenders = []
        for path in tracked("roles", "playbooks"):
            name = path.relative_to(ROOT).as_posix()
            if not name.endswith((".yml", ".yaml", ".j2", ".sh")):
                continue
            for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if "docker" not in line and "published_ports" not in line:
                    continue
                for value in self.PUBLISH.findall(line):
                    if not self.BOUND.match(value):
                        offenders.append(f"{name}:{n}: {value}")
        self.assertEqual(offenders, [], "publish on an internal address or 127.0.0.1, or use host networking")


if __name__ == "__main__":
    unittest.main()
