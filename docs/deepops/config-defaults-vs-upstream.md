# config.example defaults: this fork vs upstream NVIDIA DeepOps

How the **default settings** in `config.example/` differ from the upstream fork point
(`NVIDIA/deepops@8858399d`). This is the "what changed in the defaults, and why"
companion to [MODERNIZATION.md](../../MODERNIZATION.md) (which covers role/playbook
code). Values you set in your own gitignored `config/` always win over these.

## Install posture (group_vars/slurm-cluster.yml)

| Setting | Upstream | This fork | Why |
|---|---|---|---|
| `slurm_cluster_install_nvidia_driver` | `yes` | `true` | driver on by default (GPU-gated, so CPU nodes skip it) |
| `slurm_cluster_install_cuda` | `yes` | `false` | CUDA served as per-version Lmod modules (`nvidia_cuda_toolkit`) instead of one system CUDA |
| `slurm_install_hpcsdk` | `true` | `false` | opt-in; most sites don't need the full HPC SDK |
| `slurm_cluster_install_singularity` | `no` | `false` | opt-in (pyxis/enroot is the default container path) |
| `install_open_ondemand` | `no` | `false` | unchanged intent; boolean normalized |
| `slurm_install_nhc` | `yes` | `true` | unchanged intent; boolean normalized |

## NVIDIA driver (group_vars/all.yml)

| Setting | Upstream | This fork | Why |
|---|---|---|---|
| `nvidia_driver_branch` | `"580"` | `"595"` | newer production branch |
| open kernel modules | `nvidia_driver_ubuntu_use_open_kernel_modules: false` | `nvidia_driver_kernel_modules: "open"` | var renamed + open modules (required/recommended on recent GPUs) |
| `nvidia_driver_server_branch` | (unset) | `true` | use the `-server` packages on Ubuntu |
| `nvidia_driver_purge_existing` | (unset) | `true` | clean reinstall path |

## MPI / Slurm (group_vars/slurm-cluster.yml)

| Setting | Upstream | This fork | Why |
|---|---|---|---|
| `openmpi_configure` | v4 form: `--with-pmi=... --with-slurm=... --with-libevent=/usr` | v5 form: `--with-libevent=external` (no `--with-pmi`/`--with-slurm`) | OpenMPI 5.0.x removed those flags; PMIx is the integration path |
| `slurm_max_job_timelimit` (example) | `INFINITE` | `UNLIMITED` | the documented keyword + default for partition `MaxTime` (synonyms; UNLIMITED matches `sinfo` output) |

## NGC ready-container examples (group_vars/all.yml)

| Image | Upstream | This fork |
|---|---|---|
| cuda base | `cuda:12.4.1-base-ubuntu22.04` | `cuda:13.2.1-base-ubuntu24.04` |
| pytorch | `pytorch:24.04-py3` | `pytorch:26.05-py3` |
| tensorflow | `tensorflow:24.04-tf2-py3` | `tensorflow:25.02-tf2-py3` (the FINAL NGC TF release) |

## New configuration that did not exist upstream (group_vars/slurm-cluster.yml)

These are additive -- upstream had no equivalent default:

- **NFS** -- `nfs_exports` (server, `sync,no_root_squash,no_subtree_check`) and
  `nfs_mounts` (client, tuned for a 100GbE+ fabric: `nconnect=16`, 1 MiB rsize/wsize,
  vers 4.2). Lower `nconnect` to ~4 on 10/25GbE. The role also ships 100G socket-buffer
  sysctls (ESnet values).
- **Server-specific hardware** -- `slurm_nodes_raw` / `slurm_partitions_raw` /
  `slurm_gres_raw` examples (modern 8-GPU nodes) to inject literal node/partition/gres
  lines while keeping the role's best-practice base.
- **AI/ML slurm.conf tunables** -- `slurm_accounting_tres` (typed GPU billing),
  `slurm_tres_billing_weights`, `slurm_scheduler_parameters` (backfill), QOS preemption,
  topology, sacctmgr account/org.
- **job_submit.lua** -- bring-your-own Lua submit filter (`config.example/files/slurm/job_submit.lua`
  is a starting point) instead of routing vars.

## enroot (group_vars/all.yml) -- simplified

Upstream defined `enroot_runtime_path` / `enroot_cache_path` / `enroot_data_path` and a
full `enroot_config` block in `config.example`. Those are now **role defaults** (the
`enroot` role also provisions the sticky-1777 parent dirs via tmpfiles.d so CLI `enroot`
works), so `config.example` keeps only the NFS warning + an NVMe override example.

## Housekeeping

- Booleans normalized to YAML `true`/`false` (was `yes`/`no`).
- Fixed an upstream typo: `dashboard_metrics_scrape_tagr` -> `dashboard_metrics_scraper_tag`
  (group_vars/k8s_cluster.yml).

## Component versions (role defaults, not config.example)

For reference, the matching component pins (in `roles/*/defaults/`) advanced too:
Slurm 25.11.6, PMIx 5.0.10, OpenMPI 5.0.10, hwloc 2.13.0, enroot 4.2.0, pyxis 0.24.0,
nvidia-container-toolkit 1.19.1, DCGM-exporter 4.5.3-4.8.2, Prometheus v3.12.0,
Grafana 13.0.2, Alertmanager v0.32.2, node-exporter v1.11.1, HPC SDK 26.3,
SingularityCE 4.4.2, GPU Operator chart v26.3.2. See MODERNIZATION.md for the full list.
