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

### When the account is named something else, or is missing

`bmc_user_name` is what makes the role portable. It defaults to `ADMIN` because that is
Supermicro's factory account; other vendors ship a different one.

| Vendor | Factory account |
| --- | --- |
| Supermicro | `ADMIN` |
| Dell iDRAC | `root` |
| HPE iLO | `Administrator` |
| ASRock Rack / ASUS | `admin` |

If no account by that name exists, the role stops with a clear message rather than
creating one. Creating a BMC account means choosing a free slot, setting its privilege
and enabling it on the channel; getting that wrong locks out LAN access, and the failure
only shows up once the host is unreachable. Do it once by hand, then let the role own the
password from there:

```bash
ipmitool -I open user set name 4 svcadmin
ipmitool -I open user set password 4 '<password>' 20
ipmitool -I open user priv 4 4 1        # privilege 4 = ADMINISTRATOR, on channel 1
ipmitool -I open user enable 4
ipmitool -I open channel setaccess 1 4 link=on ipmi=on callin=on privilege=4
```

The anonymous account (user ID 1) is a separate question, and usually already answered:
check it before changing it.

```bash
ipmitool -I open user list 1
```

`IPMI Msg: false` with no channel privilege means it cannot open a session, which is
where most boards ship. Leave it alone in that state.

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
ansible-playbook playbooks/utilities/power.yml -e bmc_power_action=status
ansible-playbook playbooks/utilities/power.yml -e hostlist=gpu01 -e bmc_power_action=cycle
```

`bmc_power_action` takes `status`, `on`, `off`, `cycle`, `reset`, `soft` or `diag`. The play
runs from the control node against `bmc_ipaddr`, so it reaches a node whose OS is gone.

For the console, use `ipmitool` directly -- it is interactive, so it does not belong in
a playbook. Pass the same `-C` the role uses, or ipmitool negotiates its own and a board
that does not offer it answers `invalid role`:

```bash
IPMI_PASSWORD=... ipmitool -I lanplus -C 3 -H <bmc_ipaddr> -U ADMIN -E sol activate
IPMI_PASSWORD=... ipmitool -I lanplus -C 3 -H <bmc_ipaddr> -U ADMIN -E sel list
```

SOL leaves on `~.`, typed at the start of a line -- but so does ssh, and ssh sees it
first. Reaching the console over one ssh hop means `~~.`, or connect with `ssh -e none`
so the escape passes through. Run it inside tmux as well: a dropped ssh session takes
`ipmitool` with it, and a console is most needed exactly when the node is mid-boot.

A session killed that way can leave the payload held, which the next `sol activate`
reports as already active. `sol deactivate` clears it.

Serial-over-LAN only shows something if the node's kernel writes to the serial console,
and the GRUB menu only appears there if GRUB is told to use it too. `roles/serial_console`
does both -- run `playbooks/utilities/serial-console.yml` and reboot, or SOL will be blank
at exactly the wrong moment. Which port SOL is wired to is a BIOS setting the OS cannot
read, so confirm it by attaching during a boot.

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

## Cipher suites

`bmc_cipher_suite` is pinned rather than negotiated because ipmitool asks for the
strongest suite it knows and a BMC that does not offer it answers `invalid role` rather
than falling back. Which suites a board offers is firmware- and model-dependent, and a
mixed fleet will not agree:

```bash
ipmitool -I open lan print 1 | grep -E "Cipher Suites|Cipher Suite Priv"
```

`Cipher Suite Priv Max` maps **positionally onto the `RMCP+ Cipher Suites` list**, not
onto suite numbers: with a list of `1,2,3,6,7,8,11,12,15,16,17`, a priv string of
`aaaaaaaaXXXXXXX` means the first eight of those are administrator-capable and 15, 16
and 17 are off. Read the two lines together or the string means nothing.

Suite 3 (HMAC-SHA1 + AES-128) is the default here because it is the one every IPMI 2.0
implementation offers. Raise it only after checking that every board in the fleet lists
the suite you pick.

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
