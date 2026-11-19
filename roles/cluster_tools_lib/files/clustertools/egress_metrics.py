"""egress_metrics: what the egress policy cannot block, as node_exporter metrics.

    egress-policy-metrics CONFIG.json PATH

roles/egress_policy limits where users' connections may go. It cannot stop a user from
serving on every address, nor a tunnel over a port it allows (ngrok on 443), so this
reports them, read from /proc and the policy's nftables table:

  egress_policy_loaded                          1 when the table is in the kernel
  egress_policy_rejected_packets_total{reason}  the table's reject counters
  egress_policy_user_listener{proto,address,port,user,process,exposed}
                                                1 per non-loopback listening socket of a user
  egress_policy_tunnel_processes{user,process}  running tunnel programs
  egress_policy_metrics_timestamp_seconds       when this was measured

A user is a uid in user_uids (the policy's restricted ranges: system accounts and
root are not). exposed is "false" when the policy's inbound filter keeps the port
from outside the cluster (it is not an allowed inbound port, or the socket is bound to
an internal address), else "true". A UDP socket on an ephemeral port is a client's,
not a server's, and is left out. CONFIG holds table, user_uids ("A-B" or "N") and
tunnel_programs (a name, or "program subcommand" such as "code tunnel"). The file is
written atomically. Python 3.9 or newer, standard library only.
"""

import ipaddress
import json
import os
import pwd
import sys
import time
from collections import Counter
from pathlib import Path

from . import proc
from .cli import run_main
from .files import write_atomic
from .prom import series

# Programs whose script names the tool (node lt, python3 pagekite.py).
INTERPRETERS = {"node", "nodejs", "python", "python3", "ruby", "perl", "bash", "sh", "deno", "bun"}
TCP_LISTEN = "0A"
UDP_UNCONNECTED = "07"
NETWORKS = (ipaddress.IPv4Network, ipaddress.IPv6Network)


def uid_ranges(specs):
    out = []
    for spec in specs:
        low, _, high = str(spec).partition("-")
        out.append((int(low), int(high or low)))
    return out


def in_ranges(value, ranges):
    return any(low <= value <= high for low, high in ranges)


def decode_address(hexaddr):
    """An address as /proc/net/{tcp,udp}{,6} prints it: 32-bit words in host order."""
    raw = bytes.fromhex(hexaddr)
    words = (
        b"".join(raw[i : i + 4][::-1] for i in range(0, len(raw), 4)) if sys.byteorder == "little" else raw
    )
    return ipaddress.ip_address(words)


def is_loopback(addr):
    mapped = getattr(addr, "ipv4_mapped", None)
    return addr.is_loopback or bool(mapped and mapped.is_loopback)


def sockets(proc_root, proto, ephemeral):
    """(address, port, uid, inode) of each listening socket of PROTO."""
    found = []
    for suffix in ("", "6"):
        path = Path(proc_root, "net", proto + suffix)
        try:
            lines = path.read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if len(f) < 10:
                continue
            local, remote, state, uid, inode = f[1], f[2], f[3], int(f[7]), f[9]
            addr_hex, port_hex = local.split(":")
            port = int(port_hex, 16)
            if proto == "tcp" and state != TCP_LISTEN:
                continue
            if proto == "udp":
                if (
                    state != UDP_UNCONNECTED
                    or int(remote.split(":")[1], 16)
                    or ephemeral[0] <= port <= ephemeral[1]
                ):
                    continue
            found.append((decode_address(addr_hex), port, uid, inode))
    return found


def ephemeral_ports(proc_root):
    try:
        low, high = Path(proc_root, "sys/net/ipv4/ip_local_port_range").read_text().split()
        return int(low), int(high)
    except (OSError, ValueError):
        return 32768, 60999


def real_uid(status):
    """The real uid from /proc/PID/status (setuid programs change only the effective)."""
    for line in status.splitlines():
        if line.startswith("Uid:"):
            return int(line.split()[1])
    raise OSError("no Uid line")


def processes(proc_root, ranges):
    """{pid: (uid, names, argv)} of the processes of users in RANGES."""
    out = {}
    for entry in Path(proc_root).iterdir():
        if not entry.name.isdigit():
            continue
        try:
            uid = real_uid((entry / "status").read_text())
            if not in_ranges(uid, ranges):
                continue
            argv = [a for a in (entry / "cmdline").read_bytes().decode(errors="replace").split("\0") if a]
            names = {(entry / "comm").read_text().strip()}
        except OSError:  # gone meanwhile
            continue
        try:
            names.add(os.path.basename(os.readlink(entry / "exe")).removesuffix(" (deleted)"))
        except OSError:
            pass
        if argv:
            names.add(os.path.basename(argv[0]))
            if os.path.basename(argv[0]) in INTERPRETERS and len(argv) > 1:
                names.add(os.path.basename(argv[1]))
        out[int(entry.name)] = (uid, names, argv)
    return out


def socket_owners(proc_root, pids, inodes):
    """{inode: program name} for the sockets in INODES, from the processes in PIDS."""
    owners = {}
    for pid in sorted(pids):
        fd_dir = Path(proc_root, str(pid), "fd")
        try:
            fds = list(fd_dir.iterdir())
            comm = Path(proc_root, str(pid), "comm").read_text().strip()
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith("socket:[") and target[8:-1] in inodes:
                owners.setdefault(target[8:-1], comm)
    return owners


def tunnel_matches(names, argv, programs):
    """The tunnel programs a process is: by name, or by program and first argument."""
    hits = []
    for program in programs:
        name, _, sub = program.partition(" ")
        if sub:
            if argv and os.path.basename(argv[0]) == name and sub in argv[1:2]:
                hits.append(program)
        elif name in names:
            hits.append(program)
    return hits


def set_elements(elems):
    """An nft JSON set's elements as (low, high) pairs of ints or ip networks."""
    out = []
    for e in elems or []:
        if isinstance(e, dict) and "range" in e:
            out.append(tuple(e["range"]))
        elif isinstance(e, dict) and "prefix" in e:
            out.append(ipaddress.ip_network(f"{e['prefix']['addr']}/{e['prefix']['len']}"))
        elif isinstance(e, dict) and "elem" in e:
            out += set_elements([e["elem"]["val"]])
        elif isinstance(e, int):
            out.append((e, e))
        else:
            out.append(ipaddress.ip_network(str(e)))
    return out


def policy(table_json):
    """What the loaded table says: {loaded, counters, inbound, sets}."""
    state = {"loaded": table_json is not None, "counters": {}, "inbound": False, "sets": {}}
    for item in (table_json or {}).get("nftables", []):
        if "counter" in item:
            state["counters"][item["counter"]["name"]] = item["counter"].get("packets", 0)
        elif "chain" in item and item["chain"].get("hook") == "input":
            state["inbound"] = True
        elif "set" in item:
            state["sets"][item["set"]["name"]] = set_elements(item["set"].get("elem"))
    return state


def exposed(state, proto, addr, port):
    if not state["inbound"]:
        return True
    if not addr.is_unspecified:
        nets = state["sets"].get("internal4" if addr.version == 4 else "internal6", [])
        if any(isinstance(n, NETWORKS) and addr in n for n in nets):
            return False
    return any(low <= port <= high for low, high in state["sets"].get(f"inbound_{proto}", []))


_NAMES = {}


def user_name(uid):
    if uid not in _NAMES:
        try:
            _NAMES[uid] = pwd.getpwuid(uid).pw_name
        except KeyError:  # not in the directory (any more)
            _NAMES[uid] = str(uid)
    return _NAMES[uid]


def textfile(cfg, table_json, proc_root="/proc", now=None):
    ranges = uid_ranges(cfg["user_uids"])
    state = policy(table_json)
    ephemeral = ephemeral_ports(proc_root)
    procs = processes(proc_root, ranges)
    listening = [
        (proto, addr, port, uid, inode)
        for proto in ("tcp", "udp")
        for addr, port, uid, inode in sockets(proc_root, proto, ephemeral)
        if in_ranges(uid, ranges) and not is_loopback(addr)
    ]
    owners = socket_owners(proc_root, procs, {s[4] for s in listening})
    tunnels = Counter(
        (user_name(uid), hit)
        for uid, names, argv in procs.values()
        for hit in tunnel_matches(names, argv, cfg["tunnel_programs"])
    )
    lines = [
        "# HELP egress_policy_loaded 1 when the egress policy's nftables table is loaded.",
        "# TYPE egress_policy_loaded gauge",
        series("egress_policy_loaded", {}, int(state["loaded"])),
        "# HELP egress_policy_rejected_packets_total Connection attempts the egress policy rejected, by rule.",
        "# TYPE egress_policy_rejected_packets_total counter",
    ]
    for name, packets in sorted(state["counters"].items()):
        if name.startswith("rejected_"):
            lines.append(series("egress_policy_rejected_packets_total", {"reason": name[9:]}, packets))
    lines += [
        "# HELP egress_policy_user_listener 1 per non-loopback listening socket of a user.",
        "# TYPE egress_policy_user_listener gauge",
    ]
    seen = set()
    for proto, addr, port, uid, inode in sorted(listening, key=lambda s: (s[0], str(s[1]), s[2], s[3])):
        labels = {
            "proto": proto, "address": str(addr), "port": port, "user": user_name(uid),
            "process": owners.get(inode, ""), "exposed": str(exposed(state, proto, addr, port)).lower(),
        }  # fmt: skip
        key = tuple(labels.items())
        if key not in seen:
            seen.add(key)
            lines.append(series("egress_policy_user_listener", labels, 1))
    lines += [
        "# HELP egress_policy_tunnel_processes Running tunnel programs of a user.",
        "# TYPE egress_policy_tunnel_processes gauge",
    ]
    for (user, program), count in sorted(tunnels.items()):
        lines.append(series("egress_policy_tunnel_processes", {"user": user, "process": program}, count))
    lines += [
        "# HELP egress_policy_metrics_timestamp_seconds When these metrics were measured.",
        "# TYPE egress_policy_metrics_timestamp_seconds gauge",
        series("egress_policy_metrics_timestamp_seconds", {}, int(time.time() if now is None else now)),
    ]
    return "\n".join(lines) + "\n"


def load_table(name):
    """The table as `nft -j` prints it, or None when it is not loaded."""
    result = proc.run(
        ["nft", "-j", "list", "table", "inet", name], timeout=10, check=False, capture_output=True, text=True
    )
    return json.loads(result.stdout) if result.returncode == 0 else None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0].startswith("-"):
        print("usage: egress-policy-metrics CONFIG.json PATH  (a node_exporter textfile)", file=sys.stderr)
        return 2
    cfg = json.loads(Path(argv[0]).read_text())
    return run_main(
        "egress-policy-metrics", lambda: write_atomic(argv[1], textfile(cfg, load_table(cfg["table"])))
    )


if __name__ == "__main__":
    sys.exit(main())
