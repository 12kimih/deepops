# Managing node networking

## Why

The convention is that whatever provisions a node owns its primary interface, because
Ansible reaches the host over the network it would be rewriting. Where a provisioning
layer is in place -- MAAS, Foreman, cloud-init -- leave networking to it.

Where none is, nobody owns it, and the configuration drifts. Nodes end up with different
interface names and different subnets that no inventory records, and hand-written units
appear on one node to work around a problem the others do not have. That drift is
invisible until an outage, and then it is what makes the outage hard to read.

`roles/netplan` puts the configuration in `config/host_vars` where the rest of the node's
facts already live.

## What it manages

| Variable | Scope | Meaning |
| --- | --- | --- |
| `netplan_config` | **host** | the `network:` mapping, passed to netplan verbatim |
| `netplan_file` | site | which file the role owns, `01-netcfg.yaml` by default |
| `netplan_conflicting_manager` | site | the manager to mask so it cannot compete with the renderer |

`netplan_config` is not re-modelled as Ansible variables. Bonds, VLANs, bridges and
anything else netplan supports work without the role growing a knob for them, and there
is one schema to learn rather than two.

```yaml
# config/host_vars/<host>
netplan_config:
  version: 2
  renderer: networkd
  ethernets:
    <interface>:
      dhcp4: false
      addresses: [10.0.0.2/16]
      routes:
        - to: default
          via: 10.0.0.1
      nameservers:
        addresses: [10.0.0.1]
```

An empty `netplan_config` leaves the host's networking alone, so the role is safe to run
across an inventory where only some hosts are managed.

## Two managers, one interface

netplan renders to `systemd-networkd` or to NetworkManager. Both installed and running is
a race: whichever claims the interface first wins, and the loser reports it as unmanaged.
Set `netplan_conflicting_manager` to the one that should stay out of the way -- normally
`NetworkManager` -- and the role masks it. Masking is used rather than removal because it
is reversible and needs no package name, which differs by distribution.

## Applying it

```bash
ansible-playbook playbooks/utilities/netplan.yml --check --diff   # nothing should change
ansible-playbook playbooks/utilities/netplan.yml
```

Run the check first. Encoding what a node already has should report no change; anything
else means the encoding is wrong, and that is worth knowing before it is applied.

The play runs `serial: 1`, so a configuration that takes a node off the network takes one
node rather than the fleet. It is deliberately not part of the cluster deploy.

## When it goes wrong

netplan reads every file in the directory, so a single file cannot be validated on its
own. The role writes it, runs `netplan generate`, and puts the previous file back if
netplan refuses -- a rejected configuration never survives to a boot.

That catches malformed configuration, not wrong configuration. An address that is valid
but not reachable applies cleanly and takes the node off the network. Out-of-band access
is the recovery path: see [out-of-band management](bmc.md) for the console and power
control that make that a five-minute problem instead of a trip to the rack.
