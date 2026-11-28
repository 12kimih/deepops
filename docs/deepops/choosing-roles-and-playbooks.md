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

- `playbooks/generic/power-policy.yml` (`power_policy` role) -- holds every host's power management at full performance: the CPU governor, its EPP hint and turbo, PCIe ASPM and SATA link power at runtime, and the same plus NVMe power states on the kernel command line; on GPU hosts every GPU's power limit (the `nvidia_power_limit` cap, else the card default), default application clocks and no clock lock, with persistence mode reported. It applies at boot before slurmd and again every `power_policy_interval`, so a job or package that switches one is put back; the alerts in `roles/prometheus/files/rules/power-policy.yml` name a setting the kernel refuses (the firmware owns it) or one that keeps being switched. Nothing else changes these: no Slurm prolog or epilog part sets a CPU or GPU for a job.
- `playbooks/generic/pcie-acs.yml` (`pcie_acs` role) -- turns PCIe Access Control Services off on every device that has it, at every boot before the GPUs are opened, so GPUs below one PCIe switch talk peer to peer (NCCL's guidance for bare metal). NHC drains a node where ACS is back on; `playbooks/utilities/verify-acs.yml` reports it with the IOMMU state.

## Crash dumps

- `playbooks/generic/coredump.yml` (`coredump` role) -- hands crashing processes to **systemd-coredump** on every host, in place of apport's core handler on Ubuntu (apport itself stays). Every crash is logged to the journal; a core is stored only within the process's core size limit (`ulimit -c`, which a Slurm job takes from where it was submitted), readable by its owner with `coredumpctl`, and kept three days. `coredump_settings` sets coredump.conf over systemd's bounds.

## Disk quotas (login nodes, /home)

- `playbooks/generic/disk-quota.yml` (`disk_quota` role) -- per-user quotas on local ext4 filesystems: a login node's disk, which it may share with the controller, its database and the logs, and the home server's `/home`. Everyone in the roster is limited, administrators too; root and the system accounts never are. It counts before it limits, exports each user's usage, and takes its mount options from `host_baseline_mount_options`. See [disk-quota.md](disk-quota.md).
- `playbooks/slurm-cluster/shared-space.yml` (`shared_space` role) -- the shared part of `/home`: datasets, models and container images the account `shared` (whose home it is) writes for everyone, and the collaboration folders of `shared_projects`, one group each, published with the other cluster groups. Nothing there is removed by a deploy. See [shared-space.md](../slurm-cluster/shared-space.md).
- `playbooks/slurm-cluster/submit-holds.yml` (`submit_holds` role) -- someone at a disk hard limit cannot submit Slurm jobs until they free space, told why at submit time; running jobs go on. No one is held for a limit before its `from` day (disk_quota_filesystems), the grace for those already past it. Run `slurm.yml` after it, so `job_submit.lua` reads the holds. See [disk-quota.md](disk-quota.md#no-new-slurm-jobs-at-a-hard-limit).
- `playbooks/generic/scratch-space.yml` (`scratch_space` role) -- every person's own directory on the fast scratch space, as under /home; their space there is their user quota (`disk_quota`)
- `playbooks/generic/cluster-bot.yml` (`cluster_bot` role) -- the cluster's Slack app in Socket Mode (no port opened) for requests users make themselves (the subcommands `cluster_bot.commands` lists); needs `slack_bot_token` and `slack_app_token`. See [cluster-bot.md](../slurm-cluster/cluster-bot.md).

## Container registries (three different deploy targets)

- `standalone-container-registry` -- a Docker `registry` daemon on a **bare-metal** host. With `standalone_container_registry_cache_enable` it runs as a Docker Hub pull-through cache instead, which cannot be pushed to.
- `k8s-internal-container-registry` -- a registry deployed **into Kubernetes** via Helm.
- `nginx-docker-registry-cache` -- a **pull-through cache/proxy** that speeds up repeated image pulls (client + server).

## RDMA / InfiniBand drivers

- `mofed` -- the modern **DOCA-OFED** driver (apt/dnf repo, multi-OS). **Use this.**
  DOCA pins driver and firmware per release and does not publish every OS and
  architecture for each version: confirm `mofed_doca_version` for your adapter and OS
  on NVIDIA's DOCA downloads page, and validate on RDMA hardware, before deploying.
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

## Where each component runs

A deploy places each component by its switches and host groups, and removes it from the
hosts it no longer belongs on (with its switch, or only when it is named in
`deepops_component_removals`: explicit). The table is the registry,
`deepops_component_registry` in `roles/deploy_defaults/vars/main.yml`.

<!-- BEGIN GENERATED: components -->
| Component | Role | Switches | Hosts | Needs | Removal |
|---|---|---|---|---|---|
| `chrony` | `deepops_chrony` | `chrony_install` | `slurm-cluster`, `k8s_cluster` | - | explicit |
| `ldap_server` | `ldap_server` | `ldap_suffix` | the first `ldap-server` | - | explicit |
| `nis_server` | `nis_server` | `nis_domain` | the first `nis-master` | - | explicit |
| `nis_client` | `nis_client` | `directory_service` | `all` | - | explicit |
| `sssd_client` | `sssd_client` | `directory_service` | `all` | - | explicit |
| `rsyslog_server` | `rsyslog_server` | `kube_enable_rsyslog_server`, `slurm_enable_rsyslog_server` | the first `slurm-master`, the first `kube_control_plane` | - | with its switch |
| `rsyslog_client` | `rsyslog_client` | `kube_enable_rsyslog_client`, `slurm_enable_rsyslog_client` | `slurm-node`, `k8s_cluster` | - | with its switch |
| `fail2ban` | `fail2ban` | `kube_enable_fail2ban`, `slurm_enable_fail2ban` | `slurm-cluster`, `k8s_cluster` | - | explicit |
| `docker` | `docker` | - | - | - | explicit |
| `standalone_container_registry` | `standalone-container-registry` | `kube_enable_container_registry`, `slurm_enable_container_registry` | `slurm-master`, `kube_control_plane` | `docker` | with its switch |
| `nginx_docker_cache` | `nginx-docker-registry-cache` | `slurm_enable_nginx_docker_cache` | `slurm-cache` | `docker` | with its switch |
| `nginx_docker_cache_client` | `nginx-docker-registry-cache-client` | `slurm_enable_nginx_docker_cache` | `slurm-node` | `docker` | with its switch |
| `gpu_operator` | `nvidia-gpu-operator` | `deepops_gpu_operator_enabled` | the first `kube_control_plane` | - | with its switch |
| `k8s_gpu_plugin` | `nvidia-k8s-gpu-device-plugin` | `deepops_gpu_operator_enabled` | the first `kube_control_plane` | - | with its switch |
| `k8s_gpu_feature_discovery` | `nvidia-k8s-gpu-feature-discovery` | `deepops_gpu_operator_enabled` | the first `kube_control_plane` | - | with its switch |
| `k8s_nfs_client` | `nfs-client-provisioner` | `k8s_nfs_client_provisioner` | the first `kube_control_plane` | - | with its switch |
| `prometheus` | `prometheus` | `slurm_enable_monitoring` | `slurm-metric` | `docker` | with its switch |
| `alertmanager` | `alertmanager` | `slurm_enable_monitoring` | `slurm-metric` | `docker` | with its switch |
| `grafana` | `grafana` | `slurm_enable_monitoring` | `slurm-metric` | `docker`, `prometheus` | with its switch |
| `node_exporter` | `prometheus-node-exporter` | `slurm_enable_monitoring` | `slurm-cluster` | `docker` | with its switch |
| `ipmi_exporter` | `prometheus-ipmi-exporter` | `slurm_enable_monitoring` | `slurm-cluster` | `docker` | with its switch |
| `slurm_exporter` | `prometheus-slurm-exporter` | - | - | - | with its switch |
| `dcgm_exporter` | `nvidia-dcgm-exporter` | `slurm_enable_monitoring` | `slurm-node` | `docker`, `nvidia_container_toolkit` | with its switch |
| `io_usage` | `io_usage` | `slurm_enable_monitoring` | `slurm-cluster` | `node_exporter` | with its switch |
| `slurm_metrics` | `slurm_metrics` | `slurm_enable_monitoring` | `slurm-metric` | `node_exporter` | with its switch |
| `slurm_backup` | `slurm_backup` | `slurm_enable_backup` | the first `slurm-master` | - | with its switch |
| `gpu_diag_idle` | `gpu_diag_idle` | `install_dcgm`, `slurm_enable_gpu_diag_idle` | the first `slurm-master` | - | with its switch |
| `io_fairness` | `io_fairness` | `slurm_enable_io_fairness` | `slurm-nfs` | - | with its switch |
| `job_notice` | `job_notice` | `slurm_enable_job_notices` | `slurm-master` | - | with its switch |
| `submit_holds` | `submit_holds` | `slurm_enable_submit_holds` | `slurm-master` | - | with its switch |
| `slurm_usage_report` | `slurm_usage_report` | `slurm_enable_monitoring` | `slurm-metric` | `prometheus` | with its switch |
| `open_ondemand` | `open_ondemand` | `slurm_install_open_ondemand` | the first `slurm-master` | - | with its switch |
| `lmod` | `lmod` | `slurm_install_lmod` | `slurm-cluster` | - | with its switch |
| `cluster_tools` | `cluster_tools` | `slurm_install_cluster_tools` | `slurm-cluster` | - | with its switch |
| `nfs` | `nfs` | `k8s_deploy_nfs_server`, `slurm_enable_nfs_server` | `slurm-nfs`, the first `kube_control_plane` | - | explicit |
| `nfs_client` | `nfs_client` | `slurm_enable_nfs_client_nodes` | `slurm-nfs-client` | - | explicit |
| `nvidia_driver` | `nvidia_driver` | `deepops_gpu_operator_enabled`, `gpu_operator_preinstalled_nvidia_software`, `slurm_cluster_install_nvidia_driver` | `slurm-cluster`, `k8s_cluster` | - | explicit |
| `nvidia_container_toolkit` | `nvidia_container_toolkit` | `deepops_gpu_operator_enabled`, `gpu_operator_preinstalled_nvidia_software` | `k8s_cluster` | - | with its switch |
| `nvidia_power_limit` | `nvidia_power_limit` | `slurm_cluster_configure_gpu_power_limit` | `slurm-node` | - | with its switch |
| `pcie_acs` | `pcie_acs` | `slurm_disable_pcie_acs` | `slurm-node` | - | with its switch |
| `power_policy` | `power_policy` | `slurm_enable_power_policy` | `slurm-cluster` | - | with its switch |
| `hpcsdk` | `nvidia_hpc_sdk` | `slurm_install_hpcsdk` | the first `slurm-login` | - | with its switch |
| `cuda` | `nvidia_cuda` | `slurm_install_cuda` | `slurm-cluster` | - | with its switch |
| `cuda_toolkit` | `nvidia_cuda_toolkit` | `slurm_install_cuda_toolkit` | the first `slurm-master` | - | with its switch |
| `easybuild` | `easy-build` | `slurm_install_easybuild` | the first `slurm-master` | - | with its switch |
| `enroot` | `enroot` | `slurm_install_enroot` | `slurm-cluster` | `nvidia_container_toolkit` | with its switch |
| `pyxis` | `pyxis` | `slurm_install_enroot`, `slurm_install_pyxis` | `slurm-cluster` | `enroot` | with its switch |
| `podman` | `podman` | `slurm_install_podman` | `slurm-node` | - | with its switch |
| `container_storage_gc` | `container_storage_gc` | `slurm_enable_container_storage_gc` | `slurm-cluster` | - | with its switch |
| `openmpi` | `openmpi` | `slurm_cluster_install_openmpi` | `slurm-cluster` | - | with its switch |
| `job_scratch` | `job_scratch` | `slurm_enable_job_scratch` | `slurm-node` | - | with its switch |
| `nhc` | `nhc` | `slurm_install_nhc` | `slurm-node` | - | with its switch |
| `dcgm` | `nvidia_dcgm` | `install_dcgm` | `slurm-node` | - | with its switch |
| `dns_resolver` | `dns_resolver` | `slurm_enable_dns_resolver` | `slurm-cluster` | - | with its switch |
| `egress_policy` | `egress_policy` | `slurm_enable_egress_policy` | `slurm-cluster` | - | with its switch |
| `ingress_policy` | `ingress_policy` | `slurm_enable_ingress_policy` | `slurm-cluster` | - | with its switch |
| `coredump` | `coredump` | `kube_enable_coredump`, `slurm_enable_coredump` | `slurm-cluster`, `k8s_cluster` | - | with its switch |
| `disk_quota` | `disk_quota` | `slurm_enable_disk_quota` | `slurm-login`, `slurm-nfs`, `scratch-server` | - | with its switch |
| `login_private_dirs` | `login_private_dirs` | `slurm_enable_login_private_dirs` | `slurm-login` | - | with its switch |
| `ssh_rate` | `ssh_rate` | `slurm_enable_ssh_rate` | `slurm-login` | - | with its switch |
| `shared_space` | `shared_space` | `slurm_enable_shared_space` | the first `slurm-nfs` | - | with its switch |
| `scratch_space` | `scratch_space` | `slurm_enable_scratch_space` | `scratch-server` | - | with its switch |
| `cluster_bot` | `cluster_bot` | `slurm_enable_cluster_bot` | the first `slurm-master` | - | with its switch |
<!-- END GENERATED: components -->
