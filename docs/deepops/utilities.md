# Utility playbooks

Playbooks for running a cluster once it is deployed. None of the `playbooks/utilities/`
ones is imported by `playbooks/slurm-cluster.yml`, so each runs only when you mean it to;
the component playbooks listed beside them (power limit, ECC, PCIe ACS, CUDA toolkit,
cluster tools) also run in the deploy, and run alone to apply just that component. The configuration
playbooks converge and can be re-run; the action playbooks (power, reboot) act every time.

Most take their targets from `-e hostlist=<pattern>`, and it has to come from the command
line: a play's `hosts:` is resolved before host and group variables exist, so setting
`hostlist` in `group_vars` has no effect. `-l` narrows any playbook further.

## Out-of-band management

| Playbook | Default hosts | Does |
| --- | --- | --- |
| `utilities/power.yml` | all | chassis power through each host's BMC: `-e bmc_power_action=status\|on\|soft\|off\|cycle\|reset\|diag` |
| `utilities/bmc.yml` | all | configures each BMC in-band: address, password, SNMP community |

See [out-of-band management](bmc.md).

## Reboots and shared filesystems

| Playbook | Default hosts | Does |
| --- | --- | --- |
| `utilities/reboot.yml` | all | detaches NFS, reboots and waits for the host (roles/maintenance). It does not drain Slurm, and must not target the control node it runs from |
| `utilities/maintenance-window.yml` | slurm-master[0] | sets (or moves, or with `maintenance_window_state=absent` cancels) the Slurm maintenance reservation over the nodes, flags MAINT and IGNORE_JOBS, and posts the notice to `slack_channels.announce`; see `roles/maintenance_window` |
| `utilities/nfs-mount.yml` | all | mounts the NFS entries in each host's fstab that are not mounted, once their servers answer |

For nodes running jobs, `scontrol reboot ASAP nextstate=RESUME <nodes>` drains them,
reboots each as it empties and returns it to service. See
[Slurm and NFS](../slurm-cluster/slurm-nfs.md#mounting-reliably-at-boot).

## Node configuration

| Playbook | Default hosts | Does |
| --- | --- | --- |
| `utilities/apt-upgrade.yml` | slurm-node | rolling maintenance (roles/maintenance): one node at a time, drain it, upgrade every package (the held-back kernel, systemd, libc, GPU and scheduler stacks included), reboot if needed, check slurmd, GPUs, DCGM (level 1) and NHC, resume. Run it monthly |
| `utilities/check-id-consistency.yml` | all | asserts that the groups the directory (NIS or LDAP) publishes resolve to their pinned GID on every node; read-only |
| `utilities/fleet-consistency.yml` | slurm-cluster | are the hosts level: running kernel, shared packages, loaded NVIDIA driver, kernel policy sysctls, pending reboots, shared machine-ids; read-only |
| `utilities/mofed.yml` | all | NVIDIA DOCA-OFED for InfiniBand or RoCE adapters |
| `utilities/people-purge.yml` | slurm-nfs | removes the files (`people_purge_paths`) of everyone whose `ends` lies `people_leave_keep_days` behind, on the host where each is local, once `people_purge_confirm` names exactly them; see [accounts.md](accounts.md) |
| `utilities/retire-local-accounts.yml` | all | the last step of a move to LDAP: removes the local copies of directory accounts; see [accounts.md](accounts.md) |

## Audits

| Playbook | Default hosts | Does |
| --- | --- | --- |
| `utilities/security-audit.yml` | all | read-only security inventory of every host, compared across hosts |
| `utilities/stale-files.yml` | slurm-cluster | what retired installs left behind and what no package, rule or component claims; changes nothing |

## GPUs

| Playbook | Default hosts | Does |
| --- | --- | --- |
| `nvidia-software/nvidia-power-limit.yml` | slurm-cluster | persistent GPU power cap: the GPU model's (`nvidia_power_limit_watts_by_model`) or the host's (`nvidia_power_limit_watts`); a node with neither has its cap removed |
| `nvidia-software/nvidia-ecc.yml` | slurm-node | every GPU in the ECC mode `nvidia_ecc_mode` declares (enabled or disabled; empty leaves them); a change is set at once and applied by the next reboot, which it requests |
| `utilities/gpu-diag.yml` | slurm-node | GPU burn-in: one node at a time, drain it, run the DCGM diagnostics (`dcgmi diag -r 3`, `-e maintenance_verify_dcgm_level=N`) with the other verify checks, resume; a failing node stays drained |
| `generic/pcie-acs.yml` | slurm-cluster | PCIe ACS off at every boot for GPU peer-to-peer where `slurm_disable_pcie_acs` puts it (slurm-node), removed elsewhere; NHC drains a node where ACS is back on |
| `utilities/verify-acs.yml` | slurm-node | reports PCIe ACS (roles/pcie_acs) and IOMMU state; read-only |
| `utilities/nvidia-driver-switch.yml` | slurm-node | switches the NVIDIA driver branch one node at a time: drain, switch, reboot, verify, resume |
| `utilities/nvidia-set-gpu-clocks.yml` | slurm-node | locks the GPU clocks to `gpu_clock_lock`, or resets them (`gpu_clock_reset`), on hosts with GPUs; runtime only |
| `slurm-cluster/nvidia-cuda-toolkit.yml` | slurm-cluster | CUDA toolkits as Lmod modules, installed by the first slurm-master into the shared tree while `slurm_install_cuda_toolkit` is on, removed from it when off |

## Site tooling

| Playbook | Default hosts | Does |
| --- | --- | --- |
| `slurm-cluster/cluster-tools.yml` | slurm-cluster | site commands, shell defaults, login banner, per-user login limits and shared documentation |
