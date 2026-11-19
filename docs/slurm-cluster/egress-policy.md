# Egress policy

`roles/egress_policy` limits where users' processes may connect outside the cluster,
sets client SSH defaults for every user, optionally filters inbound traffic, and
reports what a firewall cannot block. It is off until `slurm_enable_egress_policy` is
true; `playbooks/generic/egress-policy.yml` runs it on its own.

## What it enforces

One nftables table, `inet egress_policy`, owned by this role alone. It is loaded by
`egress-policy.service` before the network comes up, and replaced in one transaction
on every change, so there is no moment without it. Docker's, Tailscale's and any
other table are left alone (the ruleset never flushes anything but its own table).

Outbound, for a **user** (a uid in `egress_policy_restricted_uids`, by default
1000-60000 and the subordinate uids above 65535 that rootless containers run as):

| Destination | Allowed |
|---|---|
| this host, the `egress_policy_internal_networks` | everything |
| anywhere else | TCP 80, 443 and 53, UDP 53 and 123 |
| anywhere else, TCP 22 | new connections through a token bucket per user and destination address (30 at once, then 20 a minute), and a node-wide one (120, then 60 a minute) |
| anywhere else, any other port | rejected at once (ICMP administratively prohibited) and logged with the uid |

Not limited: root and system accounts (below 1000), systemd's DynamicUser range and
`nobody` (60001-65535, which containerized services such as Prometheus run as),
traffic without a socket (forwarded packets, the kernel's NFS client), replies to
accepted connections, and the uids in `egress_policy_exempt_uids`: the administrators,
from the people roster. A VPN daemon running as root (tailscaled) keeps working.

This closes cloudflared's tunnel (its edge port is 7844) and ad-hoc tunnels on other
ports. A tunnel over 443 (ngrok, VS Code's `code tunnel`) cannot be told from web
traffic by port; the metrics below report it instead.

### Outbound SSH

The SSH limit exists because a client looping on a failing login (a script, an AI
agent retrying) gets the cluster's address blocked by the remote site long before a
person notices. Cluster-internal SSH is never limited. A rejected connection fails at
once with "No route to host"; `journalctl -k --grep egress-policy` shows the uid.

`/etc/ssh/ssh_config.d/40-egress-policy.conf` sets for every user (their own
`~/.ssh/config` wins, as ssh keeps the first value it reads):

- `ConnectionAttempts 1` and `ConnectTimeout 10`: one attempt, never a silent retry.
- `ControlMaster auto`, `ControlPath ~/.ssh/cm-%C`, `ControlPersist 10m`: later ssh,
  scp, rsync and git runs to the same destination reuse one connection, so they cost
  no new connection against the limit. `%C` hashes the local host name in, so homes
  shared over NFS never share a socket between nodes. These apply only where
  `~/.ssh` is writable (`Match exec`), because ssh exits when it cannot create the
  socket. `ssh -O exit HOST` closes a kept connection.

Slurm and pyxis do not use ssh, and Ansible passes its own control options on the
command line, so neither is affected.

### Inbound (replaces ufw-enable.yml)

With `egress_policy_inbound: true` the table also drops inbound traffic except
loopback, replies, ICMP, DHCP, the internal networks, `egress_policy_trusted_interfaces`
(a VPN interface) and `egress_policy_inbound_tcp_ports` / `_udp_ports`. The former
`ufw-enable.yml` playbook did this with ufw; turning this on disables ufw, so one tool
owns the host's filtering (on Ubuntu 24.04 ufw is itself iptables over nftables, and a
packet dropped by either table is dropped). The Docker bridge is no longer allowed:
the monitoring containers and the registry use host networking.

Before turning it on, the role checks the SSH session the run came in on (with a local
connection, the operator's own login) and refuses if the new rules would cut it.

Ports published with `docker run -p` are forwarded to the container, not delivered to
the host, so the input filter does not see them (ufw did not either). Bind them to
`127.0.0.1` or an internal address instead.

## Alerts

`clustertools.egress_metrics` writes node_exporter textfile metrics every minute;
`roles/prometheus/files/rules/egress-policy.yml` alerts on them:

| Alert | When |
|---|---|
| `EgressTunnelRunning` | a tunnel program (`egress_policy_tunnel_programs`) runs as a user for 5 minutes |
| `EgressListenerExposed` | a user's socket listens on a non-loopback address, on a port no inbound filter closes, for 30 minutes |
| `EgressSshRejected` | outbound SSH hit the per-user or node-wide limit in the last 10 minutes |
| `EgressPolicyNotLoaded` | the table is missing (a `flush ruleset`, a stopped unit) |
| `EgressPolicyMetricsStale` | the metrics are over 15 minutes old |

## Turning it on

1. Set the internal networks (every network jobs talk over: the cluster subnet, an
   IPoIB or storage network) and the exempt administrators in
   `config/group_vars/all.yml`:

   ```yaml
   egress_policy_internal_networks: ["{{ cluster_subnet }}"]
   egress_policy_exempt_uids: "{{ people | people_uids('admin') }}"
   ```

2. Preview on one host, apply there, test, then the rest:

   ```sh
   ansible-playbook -K --check --diff -l <host> playbooks/generic/egress-policy.yml
   ansible-playbook -K -l <host> playbooks/generic/egress-policy.yml
   sudo nft list table inet egress_policy
   ```

3. Set `slurm_enable_egress_policy: true` so the cluster deploy keeps it.

`sudo systemctl stop egress-policy` removes the table at once (until the next start or
deploy); `-e egress_policy_state=absent` removes everything the role installed.

## Variables

<!-- BEGIN GENERATED: role-variables:egress_policy -->
| Variable | Default | Description |
|---|---|---|
| `egress_policy_state` | `present` | present applies the policy; absent removes the table, its unit, the SSH drop-in and the metrics (the way back). |
| `egress_policy_internal_networks` | `[]` | The cluster's own networks (CIDRs, IPv4 or IPv6): users reach them on every port, and with egress_policy_inbound they reach this host on every port. List every network jobs talk over (an IPoIB or storage network too), or multi-node jobs break. Required. |
| `egress_policy_restricted_uids` | `[1000-60000, 65536-4294967294]` | Who is a user: the uids whose outside traffic is limited ("A-B" or "N"). Below 1000 are root and system accounts; 60001-65535 are systemd's DynamicUser range and nobody, which containerized services run as. Above 65535 are subordinate uids, which a rootless container's processes run as. |
| `egress_policy_exempt_uids` | `[]` | Users exempt from the limits: the administrators' uids, from the people roster. |
| `egress_policy_allowed_tcp_ports` | `[53, 80, 443]` | Destination ports users may reach outside the cluster: web, DNS and NTP. |
| `egress_policy_allowed_udp_ports` | `[53, 123]` |  |
| `egress_policy_ssh_ports` | `[22]` | Outbound SSH outside the cluster, limited per user and destination address by a token bucket (burst, then rate), and for the whole node: a client looping on a failing login gets the cluster's address blocked by the remote site long before a person notices. New connections only; a rejected one fails at once. |
| `egress_policy_ssh_rate` | `20/minute` |  |
| `egress_policy_ssh_burst` | `30` |  |
| `egress_policy_ssh_node_rate` | `60/minute` |  |
| `egress_policy_ssh_node_burst` | `120` |  |
| `egress_policy_log_rate` | `10/minute` | Log rejected connections to the kernel log, with the uid (journalctl -k), at most this often. |
| `egress_policy_inbound` | `false` | Filter inbound traffic too (the former ufw-enable.yml): allow loopback, replies, ICMP, DHCP, the internal networks, the trusted interfaces and the ports below, drop the rest. Turning it on stops and disables ufw, so one tool owns the host's filtering. Ports published by Docker (-p) are forwarded, not delivered to the host, and are not filtered here. |
| `egress_policy_inbound_tcp_ports` | `[]` | Ports open to everyone: a number or "A-B". The SSH port on a login host. |
| `egress_policy_inbound_udp_ports` | `[]` |  |
| `egress_policy_trusted_interfaces` | `[]` | Interfaces whose traffic is trusted like the internal networks (a VPN such as tailscale0). |
| `egress_policy_inbound_lockout_check` | `true` | Refuse to turn on inbound filtering when the SSH session Ansible (or its operator) came in on would be cut: neither from an internal network or trusted interface nor to an allowed port. |
| `egress_policy_ssh_client_options` | `{ConnectTimeout: 10, ConnectionAttempts: 1}` | Client SSH settings for every user (/etc/ssh/ssh_config.d; a user's ~/.ssh/config wins). One attempt with a timeout, so a script cannot retry in a tight loop. |
| `egress_policy_ssh_multiplex_options` | `{ControlMaster: auto, ControlPath: ~/.ssh/cm-%C, ControlPersist: 10m}` | One connection per destination, reused by every later ssh, scp and rsync to it (`ssh -O exit HOST` ends it). Only where ~/.ssh is writable: ssh exits when it cannot make the socket. {} turns it off. |
| `egress_policy_ssh_client_file` | `/etc/ssh/ssh_config.d/40-egress-policy.conf` |  |
| `egress_policy_metrics` | `true` | Listening sockets of users and running tunnel programs, as node_exporter textfile metrics (roles/prometheus/files/rules/egress-policy.yml alerts on them). |
| `egress_policy_metrics_interval` | `60s` |  |
| `egress_policy_metrics_textfile_dir` | `'{{ node_exporter_prom_dir }}'` |  |
| `egress_policy_tunnel_programs` | `[cloudflared, ngrok, frpc, frps, bore, lt, localtunnel, chisel, zrok, rathole, gost, inlets, pagekite.py, tunnelto, tmole, loophole, playit, wstunnel, devtunnel, code tunnel, code-tunnel, tailscaled, zerotier-one, wireguard-go]` | Tunnel programs, by executable or script name, or "program subcommand". |
<!-- END GENERATED: role-variables:egress_policy -->
