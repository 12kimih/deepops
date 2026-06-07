# Choosing between similar roles and playbooks

A few DeepOps roles/playbooks cover the same *domain* but do genuinely different
jobs, so they are kept separate on purpose (collapsing them would remove a
choice). This guide disambiguates the ones whose names look alike. None of these
pairs are duplicates.

## CUDA: one system toolkit vs many Lmod modules

| You want | Use | What it does |
|---|---|---|
| A single CUDA in the default PATH on each GPU node | `nvidia_cuda` role / `playbooks/nvidia-software/nvidia-cuda.yml` | Installs **one** CUDA toolkit from the NVIDIA apt/dnf repo (and the driver). Gated by `slurm_cluster_install_cuda`. |
| Several CUDA versions users pick per job | `nvidia_cuda_toolkit` role / `playbooks/slurm-cluster/nvidia-cuda-toolkit.yml` | Installs **many** CUDA versions from the official `.run` files into `/sw` as Lmod modules (`module load cuda/<ver>`). Never touches the driver. Opt-in. |

They are mutually exclusive: when serving CUDA as modules, set
`slurm_cluster_install_cuda: false`. The host **driver** is installed once by
`nvidia_driver` (`nvidia-driver.yml`) either way -- it is GPU-gated and skips
nodes with no GPU.

## Software-module build systems (all emit Lmod modulefiles)

- `lmod` -- the module engine; required by all three below.
- `easy-build` / `easy-build-packages` -- build software with **EasyBuild**.
- `spack` -- build software with **Spack**.
- `nvidia_cuda_toolkit` -- CUDA toolkits as modules (above).

Pick whichever build system you prefer; they coexist and all sit on top of `lmod`.

## GPU telemetry: agent vs exporter

- `nvidia_dcgm` -- installs the **DCGM host service** (the on-node GPU telemetry agent).
- `nvidia-dcgm-exporter` -- deploys the **Prometheus DCGM exporter** (metrics endpoint + scrape config) used by the monitoring stack.

Different layers; the monitoring stack uses the exporter.

## GPU clock control (confusingly named)

- `playbooks/utilities/gpu-clocks.yml` -- sets clock **permission** (`nvidia-smi -acp`) so users may change clocks.
- `playbooks/utilities/nvidia-set-gpu-clocks.yml` -- **locks/resets** the clock **frequency** (`nvidia-smi -lgc`/`-rgc`).

## Container registries (three different deploy targets)

- `standalone-container-registry` -- a Docker `registry` daemon on a **bare-metal** host.
- `k8s-internal-container-registry` -- a registry deployed **into Kubernetes** via Helm.
- `nginx-docker-registry-cache` -- a **pull-through cache/proxy** that speeds up repeated image pulls (client + server).

## RDMA / InfiniBand drivers

- `mofed` -- the modern **DOCA-OFED** driver (apt/dnf repo, multi-OS). **Use this.**
- `roce_backend` -- legacy k8s SR-IOV/Multus wiring; its bundled MLNX_OFED-4.7 ISO
  install is deprecated. For Kubernetes RDMA prefer `mofed` +
  `nvidia-network-operator`.

## Time synchronization

- `chrony` (`playbooks/generic/chrony-client.yml`) -- the default and only
  time-sync path. (The old `ntp-client.yml` / `geerlingguy.ntp` was removed as
  redundant.)
