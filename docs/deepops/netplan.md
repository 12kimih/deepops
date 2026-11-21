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
| `netplan_conflicting_manager` | site | the manager to mask so it cannot compete with the renderer; one masked before and no longer named is unmasked |
| `netplan_wait_online` | site | boot waits for the configured links (default on) |
| `netplan_rollback_seconds` | site | how long a host waits for Ansible before undoing a change (default 300) |

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

The cluster deploys run `playbooks/generic/netplan.yml` (one host at a time); it also
runs alone:

```bash
ansible-playbook playbooks/generic/netplan.yml --check --diff   # nothing should change
ansible-playbook playbooks/generic/netplan.yml
```

Encode what a node already has first: the managed file is compared with
`netplan_config` as data, so a node whose file already says the same (in any layout)
is left untouched, and the check reports no change. Anything else means the encoding is
wrong, and that is worth knowing before it is applied.

On a host it manages, the role takes `/etc/netplan` as its own: files no deepops role
wrote (the installer's `50-cloud-init.yaml`, a NetworkManager profile left behind) are
moved to `/var/backups/netplan/set-aside`, since netplan merges every file and a later
one overrides ours. Files other roles write (`60-bmc-mgmt.yaml`, roles/bmc) stay.

Boot waits for the configured links (`netplan_wait_online`, on by default: the NFS
mounts come after the network). A link that may stay down goes in `netplan_config` with
`optional: true`; a link netplan does not configure is not waited for.

## When it goes wrong

netplan reads every file in the directory, so a single file cannot be validated on its
own. The role writes the change, runs `netplan generate`, and puts the previous files
back if netplan refuses: a rejected configuration never survives to a boot.

A configuration that is valid but wrong (an address that cannot be reached) is caught
too: before `netplan apply` the host arms a timer (`deepops-netplan-rollback`,
`netplan_rollback_seconds`, 300 by default) that puts the previous files back and applies
them; the change is applied in the background, and only when Ansible reaches the host
again is the timer stopped. A change that cuts the host off is undone by the host itself
within five minutes. Other roles change `/etc/netplan` through the same steps
(`include_role: netplan, tasks_from: apply`).

Out-of-band access remains the last resort: see [out-of-band management](bmc.md) for the
console and power control.
