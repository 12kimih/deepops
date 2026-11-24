# Choosing between similar roles and playbooks

A few DeepOps roles and playbooks cover the same *domain* but do genuinely different
jobs, so they are kept separate on purpose (collapsing them would remove a
choice). This guide disambiguates the ones whose names look alike. None of these
pairs are duplicates.

## CUDA: one system toolkit vs many Lmod modules

| You want | Use | What it does |
|---|---|---|
| A single CUDA in the default PATH on each GPU node | `nvidia_cuda` role / `playbooks/nvidia-software/nvidia-cuda.yml` | Installs **one** CUDA toolkit from the NVIDIA apt/dnf repo (the driver is `nvidia-driver.yml`'s). Opt-in: `slurm_install_cuda: true`, then `slurm-cluster.yml` deploys it; off removes it. |
| Several CUDA versions users pick per job | `nvidia_cuda_toolkit` role / `playbooks/slurm-cluster/nvidia-cuda-toolkit.yml` | Installs **many** CUDA versions from the official `.run` files into `/sw` as Lmod modules (`module load cuda/<ver>`). Never touches the driver. Opt-in: `slurm_install_cuda_toolkit: true`, then `slurm-cluster.yml` deploys it. |

They are mutually exclusive: when serving CUDA as modules, leave
`slurm_install_cuda: false` (the default). The host **driver** is installed once by
`nvidia_driver` (`nvidia-driver.yml`) either way -- it is GPU-gated and skips
nodes with no GPU.

## Software-module build systems (all emit Lmod modulefiles)

- `lmod` -- the module engine; required by both below.
- `easy-build` -- build software with **EasyBuild**.
- `nvidia_cuda_toolkit` -- CUDA toolkits as modules (above).

Both sit on top of `lmod` and coexist. (The Spack role was retired in 2026-11.)

## GPU telemetry: agent vs exporter

- `nvidia_dcgm` -- installs the **DCGM host service** (the on-node GPU telemetry agent).
- `nvidia-dcgm-exporter` -- deploys the **Prometheus DCGM exporter** (metrics endpoint + scrape config) used by the monitoring stack.

Different layers; the monitoring stack uses the exporter.

## GPU clock control (confusingly named)

- `playbooks/utilities/nvidia-set-gpu-clocks.yml` -- **locks/resets** the clock **frequency** (`nvidia-smi -lgc`/`-rgc`) on the hosts with GPUs (`slurm-node`, or `-e hostlist=`).

## GPU power limit (persists across reboots)

- `playbooks/nvidia-software/nvidia-power-limit.yml` (`nvidia_power_limit` role) -- caps the GPU **power draw** (`nvidia-smi -pl`) and installs a `nvidia-power-limit.service` systemd unit so the cap is re-applied on every boot (nvidia-smi otherwise resets it to the card default). Set the cap per GPU model with `nvidia_power_limit_watts_by_model` (every node with that model gets it, new ones included), a host's own with `nvidia_power_limit_watts`, or `-e` for a one-off that the next run without it undoes, and target specific nodes with `-l`/`-e hostlist=`. Unlike the clock playbook above, this is a persistent, GPU-gated systemd setting, not a one-shot runtime command. The wattage lands in `/etc/nvidia-power-limit.conf`, which the systemd unit reads and the power policy (`playbooks/generic/power-policy.yml`) holds every GPU at; no job changes it.

## PCIe ACS (GPU peer-to-peer)

- `playbooks/generic/power-policy.yml` (`power_policy` role, switch `slurm_enable_power_policy`) -- holds every host's power management at full performance: the CPU governor, its EPP hint and turbo, PCIe ASPM and SATA link power at runtime, and the same plus NVMe power states on the kernel command line; on GPU hosts every GPU's power limit (the `nvidia_power_limit` cap, else the card default), default application clocks and no clock lock, with persistence mode reported. It applies at boot before slurmd and again every 15 minutes, so a job or package that switches one is put back; the alerts in `roles/prometheus/files/rules/power-policy.yml` name a setting the kernel refuses (the firmware owns it) or one that keeps being switched. Nothing else changes these: no Slurm prolog or epilog part sets a CPU or GPU for a job.
- `playbooks/generic/pcie-acs.yml` (`pcie_acs` role, switch `slurm_disable_pcie_acs`) -- turns PCIe Access Control Services off on every device that has it, at every boot before the GPUs are opened, so GPUs below one PCIe switch talk peer to peer (NCCL's guidance for bare metal). NHC drains a node where ACS is back on; `playbooks/utilities/verify-acs.yml` reports it with the IOMMU state.

## Crash dumps

- `playbooks/generic/coredump.yml` (`coredump` role, switches `slurm_enable_coredump`, `kube_enable_coredump`, on by default) -- hands crashing processes to **systemd-coredump** on every host, in place of apport's core handler on Ubuntu (apport itself stays). Every crash is logged to the journal; a core is stored only within the process's core size limit (`ulimit -c`, which a Slurm job takes from where it was submitted), readable by its owner with `coredumpctl`, and kept three days. `coredump_settings` sets coredump.conf over systemd's bounds.

## Disk quotas (login nodes, /home)

- `playbooks/generic/disk-quota.yml` (`disk_quota` role, switch `slurm_enable_disk_quota`, on `slurm-login` and `slurm-nfs`) -- per-user quotas on local ext4 filesystems: a login node's disk, which it may share with the controller, its database and the logs, and the home server's `/home`. Everyone in the roster is limited, administrators too; root and the system accounts never are. It counts before it limits, exports each user's usage, and takes its mount options from `host_baseline_mount_options`. See [disk-quota.md](disk-quota.md).
- `playbooks/slurm-cluster/shared-space.yml` (`shared_space` role, switch `slurm_enable_shared_space`, on the first `slurm-nfs`) -- the shared part of `/home`: datasets, models and container images the account `shared` (whose home it is) writes for everyone, and the collaboration folders of `shared_projects`, one group each, published with the other cluster groups. Nothing there is removed by a deploy. See [shared-space.md](../slurm-cluster/shared-space.md).
- `playbooks/slurm-cluster/submit-holds.yml` (`submit_holds` role, switch `slurm_enable_submit_holds`, on `slurm-master`) -- someone at a disk hard limit cannot submit Slurm jobs until they free space, told why at submit time; running jobs go on. No one is held before `submit_holds_from`, the grace for those already at a limit. Run `slurm.yml` after it, so `job_submit.lua` reads the holds. See [disk-quota.md](disk-quota.md#no-new-slurm-jobs-at-a-hard-limit).
- `playbooks/generic/scratch-space.yml` (`scratch_space` role, switch `slurm_enable_scratch_space`, on `scratch-server`) -- every person's own directory on the fast scratch space, as under /home; their space there is their user quota (`disk_quota`)
- `playbooks/generic/cluster-bot.yml` (`cluster_bot` role, switch `slurm_enable_cluster_bot`, on the first `slurm-master`) -- the cluster's Slack app in Socket Mode (no port opened) for requests users make themselves, such as `/scratch`; needs `slack_bot_token` and `slack_app_token`. See [cluster-bot.md](../slurm-cluster/cluster-bot.md).

## Container registries (three different deploy targets)

- `standalone-container-registry` -- a Docker `registry` daemon on a **bare-metal** host. With `standalone_container_registry_cache_enable` it runs as a Docker Hub pull-through cache instead, which cannot be pushed to.
- `k8s-internal-container-registry` -- a registry deployed **into Kubernetes** via Helm.
- `nginx-docker-registry-cache` -- a **pull-through cache/proxy** that speeds up repeated image pulls (client + server).

## RDMA / InfiniBand drivers

- `mofed` -- the modern **DOCA-OFED** driver (apt/dnf repo, multi-OS). **Use this.**
- `nvidia-network-operator` -- SR-IOV, RDMA and Multus for Kubernetes pods, from NVIDIA
  Network Operator's Helm chart (the retired `roce_backend` wired them by hand).

## Time synchronization

- `chrony` (`playbooks/generic/chrony-client.yml`) -- the default and only
  time-sync path. Every host takes time from `chrony_upstream`, or, with
  `chrony_serve_hosts` set (for example the first slurm-master), only those hosts do
  and serve the rest over `cluster_networks`, keeping the cluster in step while the
  outside is unreachable. The time zone is `locale_timezone` (roles/locale), set with
  the locale by `playbooks/generic/locale.yml`; `playbooks/generic/default-target.yml`
  sets the boot target (`default_target`, multi-user.target). All three run in both
  cluster deploys.
