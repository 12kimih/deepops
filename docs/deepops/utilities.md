# Utility playbooks

Playbooks for running a cluster once it is deployed. None of them is imported by
`playbooks/slurm-cluster.yml`, so each runs only when you mean it to. The configuration
playbooks converge and can be re-run; the action playbooks (power, reboot) act every time.

Most take their targets from `-e hostlist=<pattern>`, and it has to come from the command
line: a play's `hosts:` is resolved before host and group variables exist, so setting
`hostlist` in `group_vars` has no effect. `-l` narrows any playbook further.

## Out-of-band management

| Playbook | Default hosts | Does |
| --- | --- | --- |
| `utilities/power.yml` | all | chassis power through each host's BMC: `-e bmc_power_action=status\|on\|soft\|off\|cycle\|reset\|diag` |
| `utilities/bmc.yml` | all | configures each BMC in-band: address, password, SNMP community |
| `utilities/serial-console.yml` | all | puts the kernel and GRUB console on the BMC's serial-over-LAN, from the next boot |

See [out-of-band management](bmc.md).

## Reboots and shared filesystems

| Playbook | Default hosts | Does |
| --- | --- | --- |
| `utilities/reboot.yml` | all | detaches NFS, reboots and waits for the host. It does not drain Slurm, and must not target the control node it runs from |
| `utilities/nfs-mount.yml` | all | mounts the NFS entries in each host's fstab that are not mounted, once their servers answer |

For nodes running jobs, `scontrol reboot ASAP nextstate=RESUME <nodes>` drains them,
reboots each as it empties and returns it to service. See
[Slurm and NFS](../slurm-cluster/slurm-nfs.md#mounting-reliably-at-boot).

## Node configuration

| Playbook | Default hosts | Does |
| --- | --- | --- |
| `utilities/apt-upgrade.yml` | all | `apt dist-upgrade`; pair it with `reboot.yml` |
| `utilities/netplan.yml` | all | node networking from `netplan_config`, one host at a time; see [netplan.md](netplan.md) |
| `utilities/default-target.yml` | all | the systemd boot target, from the next boot |
| `utilities/check-id-consistency.yml` | all | asserts that NIS groups resolve to their pinned GID on every node; read-only |

## GPUs

| Playbook | Default hosts | Does |
| --- | --- | --- |
| `nvidia-software/nvidia-power-limit.yml` | slurm-node | persistent GPU power cap from `nvidia_power_limit_watts`; a host without one has its cap removed |
| `utilities/verify-acs.yml` | slurm-node | reports PCIe ACS and IOMMU state; read-only |
| `utilities/disable-acs.yml` | slurm-node | disables PCIe ACS for GPU peer-to-peer, persistently |
| `slurm-cluster/nvidia-cuda-toolkit.yml` | all | CUDA toolkits as Lmod modules, installed once into the shared tree |

## Site tooling

| Playbook | Default hosts | Does |
| --- | --- | --- |
| `slurm-cluster/cluster-tools.yml` | all | site commands, shell defaults, login banner, per-user login limits and shared documentation |
