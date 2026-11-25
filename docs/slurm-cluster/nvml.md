GPU auto-detection with Slurm and NVML
======================================

`gres.conf` holds one line, `AutoDetect=nvml` (whenever `slurm_manage_gpus` is on):
slurmd asks the [NVIDIA Management Library](https://developer.nvidia.com/nvidia-management-library-nvml)
(NVML, the driver's `libnvidia-ml`) for each node's GPUs, their device files and their
CPU affinity, so no device list is written by hand and a changed GPU or
[Multi-Instance GPU](https://www.nvidia.com/en-us/technologies/multi-instance-gpu/)
layout needs no new file. See [Slurm's GRES guide](https://slurm.schedmd.com/gres.html#AutoDetect).

The Slurm packages are built with NVML support (`gpu_nvml.so`) against NVIDIA's
`cuda-nvml-dev-<version>` package (the header and a link stub, no driver), of the
CUDA release the driver branch serves (`slurm_nvml_cuda_version`); the library itself
comes from the driver on each GPU node. A node without the driver (the controller, a
login node, a CPU node) only logs that NVML is not there.

## GPU type names

`slurm.conf` still declares how many GPUs of which type each node has
(`Gres=gpu:<type>:<count>`). With AutoDetect a type there must be the name NVML reports
for the GPU, lower case with spaces as underscores, or a substring of it: an
`NVIDIA A100-SXM4-80GB` reports `nvidia_a100-sxm4-80gb`, so `gpu:a100:8` matches. `slurmd -C` on a node prints
the detected names. A type that is no substring of the detected name (`a100sxm` for
`nvidia_a100-sxm4-80gb`) does not match, and the node is drained
for a GRES count mismatch. Either rename the type to a substring (users' `--gres` and
accounting TRES change with it), or keep the name and describe those nodes by hand,
which turns AutoDetect off for them only:

```
AutoDetect=nvml
NodeName=node[01-02] AutoDetect=off Name=gpu Type=mytype File=/dev/nvidia[0-7]
```


## Configuring slurm.conf for heterogeneous configurations

The auto-generated `slurm.conf` provided by DeepOps assumes a uniform GPU configuration across your cluster.
It does not account for multiple types of GPU hardware in the same cluster,
or for multiple different types of MIG instance.

This will result in node configuration lines in `slurm.conf` such as the following:

```
NodeName=node01  Gres=gpu:2     CPUs=2 Sockets=2 CoresPerSocket=1 ThreadsPerCore=1 Procs=2 RealMemory=15208 State=UNKNOWN
```

If you have a non-uniform GPU configuration, and especially if you have multiple types of MIG instance, you may wish to configure Slurm so that you can schedule jobs based on GPU type.
In order to distinguish GPU types, Slurm requires that you specify the number of GPUs of each type expected on the node.
This includes specifying the expected set of MIG instances on each node.

For example, if you have a node with two A100 GPUs, and in which each GPU has one `2g.20gb` and one `1g.10gb` MIG instance configured, then the `slurm.conf` line for this node might be:

```
NodeName=node01  Gres=gpu:1g.10gb:2,gpu:2g.20gb:2     CPUs=2 Sockets=2 CoresPerSocket=1 ThreadsPerCore=1 Procs=2 RealMemory=15208 State=UNKNOWN
```

To configure a custom `slurm.conf` file, instead of using the auto-generated file provided by DeepOps, see the documentation
on [configuring DeepOps](../deepops/configuration.md) and on [using static Slurm configuration](./large-deployments.md#manually-generate-static-files-for-cluster-wide-configuration).


## Configuring MIG

NVIDIA Multi-Instance GPU (MIG) is supported on specific NVIDIA GPUs such as the A100 and the A30.
This feature enables the GPU to be split into multiple distinct GPU instances, which are presented to the user as if they were distinct physical GPUs.
This is helpful especially when scheduling applications which do not need a full physical GPU to get good performance.

MIG is managed using the NVIDIA MIG Manager tool.
To install and configure this tool, you can use the [nvidia-mig.yml](../../playbooks/nvidia-software/nvidia-mig.yml) playbook in DeepOps.
For example,

```
ansible-playbook -l slurm-node -e mig_manager_profile="all-1g.10gb" playbooks/nvidia-software/nvidia-mig.yml
```

Where `mig_manager_profile` is a configuration profile for the NVIDIA `mig-parted` tool.

The playbook applies the profile the way the package's own service does: the mig-parted configuration, the hooks file and the profile go into the environment of `nvidia-mig-parted apply`. The configuration is the package's own, which it generates from the GPUs' MIG profiles at every boot (`/etc/nvidia-mig-manager/config.yaml`), so `mig_manager_profile` names one of the profiles the GPU has (`all-disabled`, `all-enabled`, `all-1g.10gb`, ...); set `mig_manager_config_src` to a reviewed file of your own for other layouts (it lands at `mig_manager_config`). The package's hooks (`/etc/nvidia-mig-manager/hooks.yaml`, which its install links to the hooks that fit the GPU) persist all three in `nvidia-mig-manager.service`'s `override.conf`, so the service reapplies the layout at every boot, and they stop and restart the GPU's services around a mode change; the playbook applies again whenever `override.conf` names another file, hooks or profile. Set `mig_manager_hooks` to a reviewed file of your own to change that (keep its `apply-start` hook, which does the persisting). Drain GPU workloads before changing the MIG configuration. On Kubernetes nodes the GPU Operator's MIG manager is in charge, and this playbook leaves them alone.

For more information on configuring MIG, see the documentation for [NVIDIA mig-parted](https://github.com/NVIDIA/mig-parted).

### MIG configuration example

#### Deploy the cluster

This walkthrough was first performed (on releases DeepOps no longer supports; DGX OS 6 or 7 today) on a small cluster consisting of the following two nodes:

- Generic white-box server running Ubuntu 20.04 for Slurm controller
- DGX A100 running DGX OS 5.2.0 for Slurm compute node

With inventory file:

```
[all]
login01
dgx01

######
# SLURM
######
[slurm-master]
login01

[slurm-nfs]
login01

[slurm-node]
dgx01

[slurm-cache:children]
slurm-master

[slurm-nfs-client:children]
slurm-node

[slurm-metric:children]
slurm-master

[slurm-login:children]
slurm-master

# Single group for the whole cluster
[slurm-cluster:children]
slurm-master
slurm-node
slurm-cache
slurm-nfs
slurm-metric
slurm-login
```

With a default configuration in `config/group_vars/slurm-cluster.yml` except for:

```yaml
mig_manager_profile: all-balanced-a100-80
```

Configured MIG on the DGX A100 by running the following:

```
ansible-playbook -b -l slurm-node playbooks/nvidia-software/nvidia-mig.yml
```

And then we can show that the MIG devices are configured:

```
user@dgx01$ nvidia-smi -L | head -n10
GPU 0: NVIDIA A100-SXM4-80GB (UUID: GPU-8e100991-ce97-6694-b8eb-b8b1ff5053af)
  MIG 3g.40gb     Device  0: (UUID: MIG-dcf43e8e-5d19-5fa8-8b1d-8aaca1e467f0)
  MIG 2g.20gb     Device  1: (UUID: MIG-6b84cf06-0978-530f-bea3-dd6108f98ebf)
  MIG 1g.10gb     Device  2: (UUID: MIG-3fff3a97-95a9-5046-9053-4c0da3d1add7)
  MIG 1g.10gb     Device  3: (UUID: MIG-7a99e994-6607-5d8a-9ab3-ace43cf9bd96)
GPU 1: NVIDIA A100-SXM4-80GB (UUID: GPU-938c4705-98bf-1735-e98b-c2a5872a8022)
  MIG 3g.40gb     Device  0: (UUID: MIG-51872ee5-5410-564e-abd2-27382756d2d4)
  MIG 2g.20gb     Device  1: (UUID: MIG-9af7553b-306e-55b4-a8c7-4cdef4066af4)
  MIG 1g.10gb     Device  2: (UUID: MIG-3407f5e5-83f9-55c0-abe5-0e2259ac50b1)
  MIG 1g.10gb     Device  3: (UUID: MIG-91e26891-6f5b-5ff3-9c66-310e47d6b059)
```

We then deploy a Slurm cluster across the two nodes by running:

```
ansible-playbook -b -l slurm-cluster playbooks/slurm-cluster.yml
```

#### MIG in slurm.conf, and nhc.conf

Because we are using MIG, the node's `Gres` must list the MIG instances by type
([Slurm's config files from settings](slurm-conf.md)). Because we have 8 physical GPUs on
the node, we have 8x 3g.40gb instances, 8x 2g.20gb instances, and 16x 1g.10gb instances;
in `config/host_vars/dgx01`:

```yaml
slurm_node_conf:
  Gres: "gpu:3g.40gb:8,gpu:2g.20gb:8,gpu:1g.10gb:16"
```

NHC does not yet support MIG, so we disable the GPU check in a copy of the node's
`nhc.conf` (the nodes are configless and keep no `/etc/slurm/slurm.conf`):

```
$ scp user@dgx01:/etc/nhc/nhc.conf config/files/slurm/nhc.conf
```

```
<  dgx01 || check_nv_gpu_count 8
---
> # dgx01 || check_nv_gpu_count 8
```

and point the role at it in `config/group_vars/slurm-cluster.yml`:

```yaml
nhc_config_template: "{{ inventory_dir }}/files/slurm/nhc.conf"
```

Then run Ansible to push this configuration back out to the cluster:

```
ansible-playbook -b -l slurm-cluster playbooks/slurm-cluster/slurm.yml
ansible-playbook -b -l slurm-cluster playbooks/slurm-cluster/nhc.yml
```

#### Testing the resulting config

Verify that Slurm sees the list of expected GPUs:

```
$ scontrol show node dgx01 | grep Gres
   Gres=gpu:3g.40gb:8(S:0-1),gpu:2g.20gb:8(S:0-1),gpu:1g.10gb:16(S:0-1)
```

Run a job on a 3g.40gb instance:

```
$ srun -N1 --gres=gpu:3g.40gb:1 nvidia-smi -L
GPU 0: NVIDIA A100-SXM4-80GB (UUID: GPU-8e100991-ce97-6694-b8eb-b8b1ff5053af)
  MIG 3g.40gb     Device  0: (UUID: MIG-dcf43e8e-5d19-5fa8-8b1d-8aaca1e467f0)
```

Run a job on a 1g.10gb instance:

```
$ srun -N1 --gres=gpu:1g.10gb:1 nvidia-smi -L
GPU 0: NVIDIA A100-SXM4-80GB (UUID: GPU-8e100991-ce97-6694-b8eb-b8b1ff5053af)
  MIG 1g.10gb     Device  0: (UUID: MIG-3fff3a97-95a9-5046-9053-4c0da3d1add7)
```
