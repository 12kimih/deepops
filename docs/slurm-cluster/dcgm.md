# DCGM

Installing NVIDIA Datacenter GPU Manager

- [DCGM](#dcgm)
  - [Introduction](#introduction)

## Introduction

[NVIDIA Datacenter GPU Manager](https://developer.nvidia.com/dcgm) is a suite of tools for managing and monitoring NVIDIA GPUs in cluster environments. It includes active health monitoring, comprehensive diagnostics, system alerts and governance policies including power and clock management. It can be used standalone by system administrators and easily integrates into cluster management, resource scheduling and monitoring products from NVIDIA partners.

DCGM is included by default on NVIDIA DGX. On other systems the [`nvidia_dcgm`](../../roles/nvidia_dcgm) role installs DCGM 4 from NVIDIA's package repositories, built for the CUDA major version of the driver branch (`datacenter-gpu-manager-4-cuda13` for a 580 or newer driver, `-cuda12` for 525 to 575), and removes the 3.x series, which stops on a CUDA 13 driver with "Detected unsupported Cuda version". There is nothing to download by hand:

- run the [nvidia-dcgm](../../playbooks/nvidia-software/nvidia-dcgm.yml) playbook directly, or
- leave `install_dcgm: true` (the example configuration's default) and `slurm-cluster.yml` runs it on the compute nodes.
