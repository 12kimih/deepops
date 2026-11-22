"""cluster_tools.egress_metrics against a fake /proc and nft table.

A user's listener on a non-loopback address is reported, with whether the inbound
filter keeps it from the outside; system accounts, loopback and UDP client sockets are
not. Tunnel programs are found by executable, script or subcommand name. The counters
come from the egress table, exposure from the ingress table (roles/ingress_policy).
"""

import ipaddress
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from cluster_tools import egress_metrics as em  # noqa: E402
from cluster_tools import prom  # noqa: E402

CFG = {"table": "egress_policy", "inbound_table": "ingress_policy", "user_uids": ["1000-60000", "65536-4294967294"],
       "tunnel_programs": ["cloudflared", "lt", "code tunnel", "tailscaled"]}  # fmt: skip
HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
TABLE = {
    "nftables": [
        {"metainfo": {"version": "1.0.9"}},
        {"table": {"family": "inet", "name": "egress_policy"}},
        {"counter": {"family": "inet", "name": "rejected_port", "table": "egress_policy", "packets": 4, "bytes": 240}},
        {"counter": {"family": "inet", "name": "rejected_ssh_node", "table": "egress_policy", "packets": 2, "bytes": 1}},
    ]
}  # fmt: skip
INBOUND = {
    "nftables": [
        {"metainfo": {"version": "1.0.9"}},
        {"table": {"family": "inet", "name": "ingress_policy"}},
        {"counter": {"family": "inet", "name": "dropped_inbound", "table": "ingress_policy", "packets": 9, "bytes": 1}},
        {"set": {"family": "inet", "name": "internal4", "table": "ingress_policy", "type": "ipv4_addr",
                 "elem": [{"prefix": {"addr": "10.0.0.0", "len": 16}}]}},
        {"set": {"family": "inet", "name": "inbound_tcp", "table": "ingress_policy", "type": "inet_service",
                 "elem": [22, {"range": [60000, 60010]}]}},
        {"chain": {"family": "inet", "table": "ingress_policy", "name": "input", "type": "filter", "hook": "input"}},
    ]
}  # fmt: skip


def sock(n, local, state, uid, inode, remote="00000000:0000"):
    return f"{n:4}: {local} {remote} {state} 00000000:00000000 00:00000000 00000000 {uid:5} 0 {inode} 1\n"


class EgressMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proc = Path(self.tmp.name)
        net = self.proc / "net"
        net.mkdir()
        (self.proc / "sys/net/ipv4").mkdir(parents=True)
        (self.proc / "sys/net/ipv4/ip_local_port_range").write_text("32768\t60999\n")
        (net / "tcp").write_text(
            HEADER
            + sock(0, "00000000:22B8", "0A", 2000, 111)  # 0.0.0.0:8888, bob
            + sock(1, "0100007F:2328", "0A", 2000, 112)  # 127.0.0.1:9000, loopback
            + sock(2, "00000000:0016", "0A", 0, 113)  # 0.0.0.0:22, root
            + sock(3, "0500000A:E6D8", "01", 2000, 117, "0100000A:0016")  # established
            + sock(4, "0500000A:1F90", "0A", 2001, 118)  # 10.0.0.5:8080, internal address
            + sock(5, "00000000:EA74", "0A", 2001, 119)  # 0.0.0.0:60020, past the inbound range
        )
        (net / "tcp6").write_text(
            HEADER
            + sock(0, "00000000000000000000000000000000:7334", "0A", 2001, 114)  # [::]:29492
            + sock(1, "00000000000000000000000001000000:1F91", "0A", 2001, 120)  # [::1]:8081
        )
        (net / "udp").write_text(
            HEADER
            + sock(0, "00000000:14E9", "07", 2000, 115)  # 0.0.0.0:5353
            + sock(1, "00000000:9C40", "07", 2000, 116)  # 0.0.0.0:40000, ephemeral
        )
        self.process(
            100, 2000, "python3", ["python3", "-m", "http.server"], "/usr/bin/python3.12", ["111", "115"]
        )
        self.process(
            101, 2001, "torchrun", ["torchrun", "train.py"], "/usr/bin/python3.12", ["114", "118", "119"]
        )
        self.process(
            200, 2000, "cloudflared", ["/home/bob/cloudflared", "tunnel", "run"], "/home/bob/cloudflared"
        )
        self.process(201, 2001, "node", ["node", "/usr/local/bin/lt", "--port", "8000"], "/usr/bin/node")
        self.process(202, 2001, "code", ["/home/dave/code", "tunnel"], "/home/dave/code")
        self.process(203, 2001, "code", ["/home/dave/code", "serve-web"], "/home/dave/code")
        self.process(300, 0, "tailscaled", ["/usr/sbin/tailscaled"], "/usr/sbin/tailscaled")
        em._NAMES.update({2000: "bob", 2001: "dave"})

    def tearDown(self):
        self.tmp.cleanup()

    def process(self, pid, uid, comm, argv, exe, inodes=()):
        d = self.proc / str(pid)
        (d / "fd").mkdir(parents=True)
        (d / "status").write_text(f"Name:\t{comm}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
        (d / "comm").write_text(comm + "\n")
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        os.symlink(exe, d / "exe")
        for n, inode in enumerate(inodes, 3):
            os.symlink(f"socket:[{inode}]", d / "fd" / str(n))

    def metrics(self, table, inbound=INBOUND):
        return prom.parse(em.textfile(CFG, table, inbound, proc_root=self.proc, now=1700000000))

    def listeners(self, values):
        return {
            (lab["proto"], lab["address"], lab["port"], lab["user"], lab["process"], lab["exposed"])
            for (name, labels), _ in values.items()
            if name == "egress_policy_user_listener"
            for lab in [dict(labels)]
        }

    def test_listeners_with_the_inbound_filter(self):
        values = self.metrics(TABLE)
        self.assertEqual(values[prom.key("egress_policy_loaded")], "1")
        self.assertEqual(
            self.listeners(values),
            {
                ("tcp", "0.0.0.0", "8888", "bob", "python3", "false"),
                ("tcp", "10.0.0.5", "8080", "dave", "torchrun", "false"),
                ("tcp", "0.0.0.0", "60020", "dave", "torchrun", "false"),
                ("tcp", "::", "29492", "dave", "torchrun", "false"),
                ("udp", "0.0.0.0", "5353", "bob", "python3", "false"),
            },
        )

    def test_an_allowed_inbound_port_is_exposed(self):
        OPEN = {"nftables": [*INBOUND["nftables"][:-2],
                {"set": {"name": "inbound_tcp", "elem": [{"range": [8000, 8888]}]}}, INBOUND["nftables"][-1]]}  # fmt: skip
        self.assertIn(
            ("tcp", "0.0.0.0", "8888", "bob", "python3", "true"), self.listeners(self.metrics(TABLE, OPEN))
        )

    def test_an_internal_address_range_is_internal(self):
        """auto-merge prints adjacent networks as one address range: still internal."""
        MERGED = {"nftables": [*INBOUND["nftables"][:3],
                  {"set": {"name": "internal4", "elem": [{"range": ["9.255.0.0", "10.0.255.255"]}, "192.0.2.7"]}},
                  {"set": {"name": "inbound_tcp", "elem": [{"range": [8000, 8888]}]}},
                  INBOUND["nftables"][-1]]}  # fmt: skip
        # 8080 is open from outside: only the internal address keeps it in.
        self.assertIn(
            ("tcp", "10.0.0.5", "8080", "dave", "torchrun", "false"),
            self.listeners(self.metrics(TABLE, MERGED)),
        )
        state = em.policy(MERGED)
        self.assertTrue(em.in_set(state, "internal4", ipaddress.ip_address("192.0.2.7")))
        self.assertFalse(em.in_set(state, "internal4", ipaddress.ip_address("10.1.0.0")))
        self.assertFalse(em.in_set(state, "internal4", 8080))

    def test_without_the_tables_every_listener_is_exposed(self):
        values = self.metrics(None, None)
        self.assertEqual(values[prom.key("egress_policy_loaded")], "0")
        self.assertEqual({entry[5] for entry in self.listeners(values)}, {"true"})
        self.assertFalse([k for k in values if k[0] == "egress_policy_rejected_packets_total"])
        # The egress table alone filters nothing inbound.
        self.assertEqual({entry[5] for entry in self.listeners(self.metrics(TABLE, None))}, {"true"})

    def test_reject_counters(self):
        values = self.metrics(TABLE)
        self.assertEqual(values[prom.key("egress_policy_rejected_packets_total", reason="port")], "4")
        self.assertEqual(values[prom.key("egress_policy_rejected_packets_total", reason="ssh_node")], "2")
        self.assertNotIn(prom.key("egress_policy_rejected_packets_total", reason="dropped_inbound"), values)

    def test_tunnels_by_name_script_and_subcommand_not_root(self):
        values = self.metrics(TABLE)
        tunnels = {dict(labels)["process"]: (dict(labels)["user"], v)
                   for (name, labels), v in values.items() if name == "egress_policy_tunnel_processes"}  # fmt: skip
        self.assertEqual(
            tunnels, {"cloudflared": ("bob", "1"), "lt": ("dave", "1"), "code tunnel": ("dave", "1")}
        )

    def test_addresses_decode_in_host_order(self):
        self.assertEqual(str(em.decode_address("0100007F")), "127.0.0.1")
        self.assertEqual(str(em.decode_address("00000000000000000000000001000000")), "::1")
        self.assertTrue(em.is_loopback(em.decode_address("0000000000000000FFFF00000100007F")))


if __name__ == "__main__":
    unittest.main()
