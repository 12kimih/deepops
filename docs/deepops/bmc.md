# Out-of-band management (BMC / IPMI)

## Why

A node whose operating system is gone -- hung partway through a shutdown, stopped at a
boot prompt, panicked -- answers nothing on the cluster network. Its BMC does: it runs
on standby power and stays reachable whenever the machine has AC. It is the only way to
read the console or cycle power without walking to the rack.

The catch is that a BMC is only useful if it was configured *before* it was needed. A
board that still holds its factory settings has no usable address, and on hardware
shipped since about 2019 the factory password is a per-board value printed on a sticker
inside the chassis. Discovering all of that during an outage costs hours.

`roles/bmc` removes that problem by configuring each host's BMC from the host itself.

## How the two paths differ

**In-band (KCS).** A host reaches its own BMC through `/dev/ipmi0`, over a bus on the
motherboard. No network, and no BMC credentials -- being root on the host is the
authorisation. This is what the role uses, and it is why a board whose factory password
exists only on a sticker can still be brought under management from Ansible.

**Out-of-band (LAN).** Everything reached from another machine -- serial-over-LAN,
power control, the web interface -- needs the BMC's own IP address and credentials.
Those are exactly what the in-band step establishes.

So the order is: configure in-band while the nodes are up, then rely on out-of-band
when they are not.

## What the role manages

| Variable | Scope | Meaning |
| --- | --- | --- |
| `bmc_channel` | site | IPMI LAN channel, almost always `1` |
| `bmc_user_name` | site | BMC account to manage; its numeric ID is derived from this |
| `bmc_ipsrc` | site | `static` or `dhcp` |
| `bmc_ipaddr` | **host** | the BMC's address, in `host_vars` |
| `bmc_netmask`, `bmc_gateway` | site | the rest of the LAN configuration |
| `bmc_password` | site | vault this; empty means "leave the password alone" |
| `bmc_password_format` | site | `20` on newer BMCs, `16` on older ones |
| `bmc_mgmt_interface`, `bmc_mgmt_address` | **host** | this host's leg on the management network |

Every value is empty by default, and an empty value means the role leaves that setting
alone. Running it against a host with no BMC, or a VM, is a no-op.

## How it stays idempotent

- **LAN settings** are read back with `ipmitool lan print` and written only where they
  differ from the desired value.
- **The password cannot be read back at all.** `ipmitool user test` verifies a password
  without changing it, so the role tests first and writes only when the test fails.
  This is what keeps repeated runs from reporting a change every time.
- **The user ID** is looked up from `bmc_user_name`, so the account is named in one
  place rather than configured twice and allowed to disagree.

## Procedure

### 1. Plan the addresses

Pick a range on the management network and assign one address per node in
`config/host_vars/<host>`:

```yaml
bmc_ipaddr: 192.168.0.11
```

Assigning them yourself is what makes the node-to-BMC mapping knowable: the role writes
each address from the node that owns it, so there is nothing to discover afterwards.

### 2. Set the site-wide values

In `config/group_vars/all.yml`:

```yaml
bmc_netmask: 255.255.255.0
bmc_gateway: 192.168.0.1
```

### 3. Choose a password and vault it

```bash
ansible-vault encrypt_string 'your-password' --name bmc_password
```

Paste the result into `config/group_vars/all.yml`. Newer BMCs enforce a complexity
policy -- 8 to 20 characters with mixed case, a digit and a symbol is a safe target.

### 4. Apply

```bash
ansible-playbook playbooks/utilities/bmc.yml
```

This playbook is deliberately **not** imported by `playbooks/slurm-cluster.yml`. It
writes firmware settings rather than OS state, and a wrong `bmc_ipaddr` moves a BMC out
of reach, so it is run on purpose rather than as a side effect of a cluster deploy.

The control node also needs a route to the management network. Set
`bmc_mgmt_interface` and `bmc_mgmt_address` on it and the role writes
`/etc/netplan/60-bmc-mgmt.yaml`, leaving the site's own netplan files untouched.

### 5. Verify

```bash
ansible-playbook playbooks/utilities/power.yml            # power status, every host
```

A second run of `bmc.yml` should report no changes.

## Power control

```bash
ansible-playbook playbooks/utilities/power.yml -e power_action=status
ansible-playbook playbooks/utilities/power.yml -e hostlist=gpu01 -e power_action=cycle
```

`power_action` takes `status`, `on`, `off`, `cycle`, `reset`, `soft` or `diag`. The play
runs from the control node against `bmc_ipaddr`, so it reaches a node whose OS is gone.

For the console, use `ipmitool` directly -- it is interactive, so it does not belong in
a playbook:

```bash
IPMI_PASSWORD=... ipmitool -I lanplus -H <bmc_ipaddr> -U ADMIN -E sol activate   # ~. to exit
```

Serial-over-LAN only shows something if the node's kernel writes to the serial console.
Adding `console=ttyS0,115200` to the kernel command line is worth doing before you need
it, or SOL will be blank at exactly the wrong moment.

## When the BMC addresses are unknown

If you inherit hardware that was never configured, the in-band procedure above is still
the shortest path: it needs no BMC address at all. Fall back on discovery only for
boards whose host will not boot.

With the control node patched into the management switch, every BMC answers IPv6
all-nodes multicast regardless of its IPv4 configuration:

```bash
ip link set <iface> up
ping -6 -c3 -I <iface> ff02::1
ip -6 neigh show dev <iface>
```

Each responder's link-local address works as an `ipmitool -H` target when suffixed with
the interface: `-H 'fe80::...%<iface>'`. The TLS certificate on port 443 names the
vendor, which tells you which default credentials to try.

## Caveats

- **Wrong passwords lock the account.** BMCs typically disable an account after three
  bad attempts for several minutes. Test credentials against one BMC, not all of them,
  and never sweep an inventory with a guess.
- **`netplan apply`** runs on the control node when the management interface changes.
  It only adds an interface, but it does touch networking.
- **The password appears briefly in the managed host's process arguments** while
  `ipmitool` runs. The tasks set `no_log`, so it stays out of Ansible's output.
- **Changing a BMC's LAN address drops any active out-of-band session.** The role runs
  in-band, so it is unaffected, but a console you have open elsewhere will disconnect.
