# DCGM

Installing NVIDIA Datacenter GPU Manager

- [DCGM](#dcgm)
  - [Introduction](#introduction)

## Introduction

[NVIDIA Datacenter GPU Manager](https://developer.nvidia.com/dcgm) is a suite of tools for managing and monitoring NVIDIA GPUs in cluster environments. It includes active health monitoring, comprehensive diagnostics, system alerts and governance policies including power and clock management. It can be used standalone by system administrators and easily integrates into cluster management, resource scheduling and monitoring products from NVIDIA partners.

DCGM is included by default on NVIDIA DGX. On other systems the [`nvidia_dcgm`](../../roles/nvidia_dcgm) role installs it (`datacenter-gpu-manager`) from NVIDIA's package repositories, so there is nothing to download by hand:

- run the [nvidia-dcgm](../../playbooks/nvidia-software/nvidia-dcgm.yml) playbook directly, or
- leave `install_dcgm: true` (the example configuration's default) and `slurm-cluster.yml` runs it on the compute nodes.
