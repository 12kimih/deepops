# Slurm Deployment Guide

Instructions for deploying a GPU cluster with Slurm

- [Slurm Deployment Guide](#slurm-deployment-guide)
  - [Requirements](#requirements)
  - [Installation Steps](#installation-steps)
  - [Slurm Validation](#slurm-validation)
  - [Using Slurm](#using-slurm)
  - [Customizing the Slurm configuration](#customizing-the-slurm-configuration)
  - [Prolog and Epilog](#prolog-and-epilog)
  - [Node Health Check](#node-health-check)
  - [Monitoring Slurm](#monitoring-slurm)
  - [Centralized syslog](#centralized-syslog)
  - [Configuring shared filesystems](#configuring-shared-filesystems)
  - [Installing tools and applications](#installing-tools-and-applications)
  - [Open OnDemand](#open-ondemand)
  - [Pyxis and Enroot](#pyxis-and-enroot)
  - [Large deployments](#large-deployments)
  - [Identity across the cluster](#identity-across-the-cluster)
  - [Rebooting the cluster](#rebooting-the-cluster)
  - [When a node will not finish booting](#when-a-node-will-not-finish-booting)
  - [Out-of-band management](#out-of-band-management)
  - [Utility playbooks](#utility-playbooks)
  - [Uninstalling Slurm](#uninstalling-slurm)

## Requirements

- Control system to run the install process
- One server to act as the Slurm controller/login node
- One or more servers to act as the Slurm compute nodes

## Installation Steps

1. Install a supported operating system on all nodes.

   Install a supported operating system on all servers via a 3rd-party solution such as [MAAS](https://maas.io/) or [Foreman](https://www.theforeman.org/), or via an existing site-standard automated installer.
   For new Ubuntu 24.04 or DGX OS 7 deployments, prefer Ubuntu autoinstall/cloud-init or MAAS and then apply DeepOps after the OS is present.

2. Set up your provisioning machine.

   This will install Ansible and other software on the provisioning machine which will be used to deploy all other software to the cluster. For more information on Ansible and why we use it, consult the [Ansible Guide](../deepops/ansible.md).

   ```bash
   # Install software prerequisites and copy default configuration
   ./scripts/setup.sh
   ```

3. Create and edit the Ansible inventory.

   Ansible uses an inventory which outlines the servers in your cluster. The setup script from the previous step will copy an example inventory configuration to the `config` directory.

   Edit the inventory:

   ```bash
   # Edit inventory
   # Add Slurm controller/login host to `slurm-master` group
   # Add Slurm worker/compute hosts to the `slurm-node` groups
   vi config/inventory

   # (optional) Modify `config/group_vars/*.yml` to set configuration parameters
   ```

   > Note: Multiple hosts can be added to the `slurm-master` group for high-availability. You must also set
   > `slurm_enable_ha: true` in `config/group_vars/slurm-cluster.yml`. For more information about HA Slurm deployments,
   > see: https://slurm.schedmd.com/quickstart_admin.html#HA

4. If running on a cluster where you intend to configure [Multi-Instance GPU](https://www.nvidia.com/en-us/technologies/multi-instance-gpu/), consult the [Slurm NVML documentation](./nvml.md).

5. Verify the configuration.

   ```bash
   ansible all -m raw -a "hostname"
   ```

6. Install Slurm.

   ```bash
   # NOTE: If SSH requires a password, add: `-k`
   # NOTE: If sudo on remote machine requires a password, add: `-K`
   # NOTE: If SSH user is different than current user, add: `-u ubuntu`
   ansible-playbook -l slurm-cluster playbooks/slurm-cluster.yml
   ```

## Slurm Validation

A Slurm validation playbook is provided. Please refer to
"[slurm-validation.yml](../../playbooks/slurm-cluster/slurm-validation.yml)".

The validation playbook verifies that Pyxis and Enroot can run GPU jobs across
nodes by running the NCCL all-reduce test. Its parameters, each with its default and
meaning, are the `vars:` at the top of the playbook; override any with `-e`:

| Parameter | What it sets |
|---|---|
| `base_container` | the image the NCCL tests are built in: `ngc_ready_pytorch_container` (pinned NGC PyTorch, [versions.md](../deepops/versions.md)) by default, or a `.sqsh` path |
| `compile_nccl_tests`, `nccl_tests_container` | build the tests into this `.sqsh` file (default), or run a container that already has them |
| `num_nodes`, `num_gpus`, `partition`, `timelimit` | the job's shape; empty `num_nodes` takes every idle node in the partition |
| `allreduce_command`, `srun_exports`, `srun_options` | the test command and extra `srun` settings |
| `cleanup` | delete the built `.sqsh` afterwards |

The `base_container` must already have NCCL and MPI.

Please refer to the following examples and adopt for your environment.

NOTE: This will use Pyxis to download a container.

1. Example to run on all idle nodes with default behavior.

   ```bash
   ansible-playbook -l slurm-cluster playbooks/slurm-cluster/slurm-validation.yml
   ```

   This will create a container "`${HOME}/enroot_images/nccl_tests_slurm_val.sqsh`"
   which has to be manually deleted later if desired.

2. Example to run on 2 nodes, use a custom location
   for compiled nccl container, disable UCX and HCOLL, then cleanup.

   ```bash
   ansible-playbook -l slurm-cluster playbooks/slurm-cluster/slurm-validation.yml \
     -e '{nccl_tests_container: "${HOME}/enroot_images/nccl_tests_torch_val.sqsh"}' \
     -e '{num_nodes: 2}' \
     -e '{srun_exports: "NCCL_DEBUG=INFO,OMPI_MCA_pml=^ucx,OMPI_MCA_coll=^hcoll"}' \
     -e '{cleanup: True}'
   ```

3. Example to run on 1 node using an existing NCCL test container from a site registry.
   ```bash
   ansible-playbook -l slurm-cluster playbooks/slurm-cluster/slurm-validation.yml \
     -e '{nccl_tests_container: registry.example.com/hpc/nccl-tests:latest}' \
     -e '{compile_nccl_tests: False}' \
     -e '{num_nodes: 1}'
   ```

Pay attention to the playbook output in the terminal. The NCCL compilation and srun command will be printed. Pyxis and PMI are used with srun for orchestrating containers and multinode MPI. The results of "Out of bounds values" and "Avg bus bandwidth" are printed. The "Out of bounds values" should be 0 otherwise the test is considered FAIL. The bandwidth will vary depending on the network. The NCCL allreduce test results are written out to "`/tmp/nccl_tests.out`" after a successful playbook run. If running NCCL tests fails the error results are saved to "`/tmp/nccl_tests.err`". Refer to these file for detailed analysis.

## Using Slurm

Now that Slurm is installed, try a ["Hello World" example using MPI](../../workloads/examples/slurm/mpi-hello/README.md).

Read through the [slurm usage guide](slurm-usage.md) for more information.

## Customizing the Slurm configuration

The `slurm.conf`, `cgroup.conf`, `gres.conf`, `slurmdbd.conf`, and (optional)
`job_submit.lua` files come from templates in the `slurm` role. By default the role
generates them from gathered facts; to take full control, drop your own **complete**
template files under `config/files/slurm/` and point the role at them in your
git-untracked `config/group_vars/slurm-cluster.yml`:

```yaml
slurm_conf_template:        "{{ inventory_dir }}/files/slurm/slurm.conf.j2"
slurm_cgroup_conf_template: "{{ inventory_dir }}/files/slurm/cgroup.conf.j2"
slurm_gres_conf_template:   "{{ inventory_dir }}/files/slurm/gres.conf.j2"
slurm_dbd_conf_template:    "{{ inventory_dir }}/files/slurm/slurmdbd.conf.j2"
```

Each file is rendered verbatim (Jinja still works, e.g. `{{ slurm_db_password }}` from
the vault), so your whole cluster config -- node/partition/gres lines and every
`slurm.conf` option -- lives in one place you control, instead of scattered across role
variables. These files are server-specific, so keep them in `config/` (git-ignored).
They live on the controller only: the nodes fetch them from `slurmctld` (configless).
The controller refuses a `slurm.conf` template without `enable_configless` in
`SlurmctldParameters`, with a `ClusterName` other than `{{ slurm_cluster_name }}`, or
with a `PluginDir` other than the packages'; leave `PluginDir` out. Keep
`MetricsType=metrics/openmetrics` for the Prometheus scrape.

Optional submit-time routing (e.g. GPU jobs to per-GPU-type partitions) is available
by setting `slurm_job_submit_plugins: "lua"` and the site's `slurm_job_submit_policy`
(see `config.example/group_vars/slurm-cluster.yml`). The role renders
`roles/slurm/templates/etc/slurm/job_submit.lua.j2` from it: it routes by GPU type and
sends CPU-only jobs to a CPU partition; optionally it also pins GPU jobs to every
partition of their type (`force_gpu_partition`), sizes them by the partition defaults
alone (`gpu_jobs_use_defaults`: CPU options dropped, memory options rejected, at most
one task per GPU) and keeps interactive GPU sessions on a QoS matching
`interactive_gpu_qos`. The logic is the same for every cluster; only the policy
differs. `scripts/check.sh` renders it and runs `roles/slurm/tests/job_submit_test.lua`
on the result. A site's own Lua file still works: point `slurm_job_submit_template` at
a path not ending in `.j2` and it is installed as it is.

Because `config/` is git-ignored by DeepOps, keep your server-specific settings
and secrets there and manage that directory as a separate **private** repo:

```sh
scripts/setup.sh    # starts config/ from config.example (docs/deepops/configuration.md)
cd config && git init && git remote add origin git@github.com:yourorg/deepops-config.git
ansible-vault encrypt_string --prompt --name slurm_db_password   # vault each secret inline
```

See the comments in
[`config.example/group_vars/slurm-cluster.yml`](../../config.example/group_vars/slurm-cluster.yml)
for ready-to-copy examples.

## Prolog and Epilog

The default Slurm deployment includes a collection of prolog and epilog scripts that should be modified to suit a particular system.
For more information, see the [prolog/epilog documentation](slurm-prolog-epilog/README.md).

From release 26.11 the cleanup that runs when a user's job ends is job-scoped by default and no longer touches the user's other running jobs on the node; `slurm_epilog_cleanup_scope: user` restores the previous behaviour for one release. See [Job-scoped epilog cleanup](epilog-job-scoped-cleanup.md).

### Lua plugins

The Slurm packages are always built with Lua 5.4 (`liblua5.4`), so Lua plugins such as
`JobSubmitPlugins=lua` or `CliFilterPlugins=lua` load without any setting. Point
`slurm_job_submit_template` at your `job_submit.lua` (it goes to the controller only,
the one daemon that runs it) and test it with the same Lua slurmctld runs:
`lua5.4 roles/slurm/tests/job_submit_test.lua <path>`. See the
[Slurm documentation](https://slurm.schedmd.com/job_submit_plugins.html).

## How Slurm is installed

The `slurm` role installs SchedMD's own Debian packages (`slurm-smd*`), built from the
checksummed release tarball (`slurm_checksums`) with the `debian/` directory it ships,
the way [Slurm's admin guide](https://slurm.schedmd.com/quickstart_admin.html#debuild)
describes. `playbooks/slurm-cluster/slurm.yml` builds them once per version on
`slurm-master[0]`, as an unprivileged account, against the distribution's PMIx, hwloc,
Lua 5.4, curl, InfiniBand, MUNGE, MariaDB and PAM libraries and NVIDIA's NVML package,
and publishes them to a signed apt repository on shared storage (`slurm_repo_dir`).
Every node installs exactly `slurm_version` from it; apt holds it there.

The nodes keep no copy of the configuration: `slurmctld` serves `slurm.conf`,
`gres.conf`, `cgroup.conf` and `plugstack.conf`
([configless](https://slurm.schedmd.com/configless_slurm.html)), `slurmd` fetches them
with `--conf-server`, login nodes through `sackd`. A configuration change on the
controller reaches them with `scontrol reconfigure`, which the role runs only once the
restarted `slurmctld` answers `scontrol ping`. A configuration it refuses never reaches
the nodes: the role puts the controller's previous files back, restarts `slurmctld` on
them and fails with its journal (`roles/slurm/tasks/controller_restart.yml`). Prolog and
epilog scripts stay on each node (configless ships only single files).

`slurmctld` serves its own metrics for Prometheus (`MetricsType=metrics/openmetrics`,
`/metrics/jobs`, `/metrics/nodes`, `/metrics/partitions`, `/metrics/scheduler` on its
port), which `roles/prometheus` scrapes.

Changing `slurm_version` on a running cluster, the first move from an older source
build included, is [the upgrade playbook](slurm-upgrade.md); a plain deploy refuses it.

## Node Health Check

The default Slurm deployment includes setting up [Node Health Check](https://github.com/mej/nhc). Slurm runs it on every node every five minutes (`HealthCheckInterval`, in any node state) to validate that the hardware and software is set up as expected. Nodes which fail this check will be automatically drained in Slurm to prevent jobs running on potentially broken nodes.

However, the default configuration that is generated by DeepOps is very basic, checking that CPU, memory, and GPUs are present, that a few essential services are running, and that no GPU has a fault that needs a reset. To customize this file, you can set the `nhc_config_template` variable to point to your custom file.
The [NHC docs](https://github.com/mej/nhc/blob/master/README.md) go into detail about the configuration language.

The generated configuration also asserts that the shared filesystems are mounted read-write, so that a node which lost one is drained rather than collecting failed jobs.
The list comes from `nhc_check_mounts`, which defaults to the mountpoints in `nfs_mounts` so that it stays in step with what the `nfs` role mounts; set it explicitly to check a filesystem DeepOps does not manage, or to `[]` to skip the check.
Each mountpoint gets a `check_file_test` before its `check_fs_mount_rw`: that walks into the directory, which triggers an `x-systemd.automount` entry nothing has accessed yet (see [slurm-nfs.md](slurm-nfs.md#mounting-reliably-at-boot)).

On GPU nodes it drains a node whose current boot logged an XID listed in `nhc_gpu_drain_xids`: by default the XIDs whose immediate action in NVIDIA's [XID catalog](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html) is a GPU reset or a reboot (a GPU fallen off the bus, uncontained ECC, GSP errors...).
Faults of the application itself (XID 13, 31, 43, 45, 94) leave the node in service.
It also drains a node whose row remapping is pending (applied at the next GPU reset) or has failed (no spare rows left, NVIDIA's RMA criterion; `nhc_gpu_check_remapped_rows`).
The drain reason names the XID and the GPU's PCI address. Reboot the node once its jobs end: NHC returns it to service by itself when the new boot is clean. A failed row remapping stays drained until the GPU is replaced.

To turn NHC off, set `slurm_install_nhc: false`: `slurm_health_check_program` follows it.

## Monitoring Slurm

As part of the Slurm installation, Grafana and Prometheus are both deployed.

The services can be reached from the following addresses:

- Grafana: http://\<slurm-metric\>:3000 (`grafana_port`)
- Prometheus: http://\<slurm-metric\>:9090 (`prometheus_port`)

## Centralized syslog

The cluster nodes forward their syslog to the first Slurm controller by default; set these to false in your DeepOps configuration to turn it off:

```
slurm_enable_rsyslog_server: false
slurm_enable_rsyslog_client: false
```

For more information about our syslog forwarding functionality, please see the [centralized syslog guide](../misc/syslog.md).

## Configuring shared filesystems

For information about configuring a shared NFS filesystem on your Slurm cluster, see the documentation on [Slurm and NFS](./slurm-nfs.md).

## Installing tools and applications

You may optionally choose to install a tool for managing additional packages on your Slurm cluster.
See the documentation on [software modules](./software-modules.md) for information on how to set this up.

## Open OnDemand

The [Open OnDemand](https://openondemand.org/) web portal is a component, off by default: set `slurm_install_open_ondemand: true` and the deploy (or `playbooks/slurm-cluster/open-ondemand.yml` alone) installs it on the Slurm controller and removes it from every other host. Its settings, the Dex login and the TLS certificate are in [roles/open_ondemand/README.md](../../roles/open_ondemand/README.md).

## Pyxis and Enroot

[Pyxis](https://github.com/NVIDIA/pyxis) and [Enroot](https://github.com/NVIDIA/enroot) are installed by default and can be disabled by setting `slurm_install_enroot` and `slurm_install_pyxis` to no. They are the supported, release-validated container runtime for Slurm in DeepOps.

The per-user directories containers keep around jobs, and what creates and removes each, are listed in [the job environment](./job-environment.md).

To build images from a Dockerfile, users run rootless Podman in a CPU job and import the result with `enroot import podman://IMAGE`; see [Building container images with rootless Podman](../container/podman.md). It is installed on the compute nodes by default (`slurm_install_podman`) and replaces the retired docker-rootless role.

The DeepOps Singularity wrapper role has been retired. Singularity lives on upstream as [Apptainer](https://apptainer.org/); sites that still want it can install Apptainer/Singularity separately.

## GPU graphics and video

The full NVIDIA driver is installed, so jobs can render on the GPUs (OpenGL, EGL or
Vulkan, with no display) and encode and decode video on them (NVENC, NVDEC);
`nvidia_driver_headless` keeps only CUDA. See [GPU graphics and video in jobs](./gpu-graphics.md).

## Large deployments

To minimize the requirements for the cluster management services, DeepOps deploys a single Slurm head node for cluster management, shared filesystems, and user login. However, for larger deployments, it often makes sense to run these functions on multiple separate machines.
For instructions on separating these functions, see the [large deployment guide](./large-deployments.md).

## Identity across the cluster

Slurm resolves a job's supplementary groups on the submit node and ships the resulting
**numeric** GIDs into the job, while a direct SSH login resolves them on the node
itself. Anything that depends on group membership therefore has to be made consistent
deliberately, or it will behave differently depending on how the user arrived:

- [Docker access from inside Slurm jobs](./docker-in-slurm-jobs.md) -- why adding a user
  to the `docker` group on a compute node has no effect inside a job, and how to
  publish a socket group that does work.
- [Administrator sudo on a Slurm cluster with a directory](./cluster-sudo-netgroup.md) -- why
  `sudo` group membership gives two different answers on the same node, and how a
  directory (NIS or LDAP) netgroup avoids it.

`playbooks/utilities/check-id-consistency.yml` asserts that every group the
directory (NIS or LDAP) publishes resolves to its pinned GID on every node.

## Egress policy

[Egress policy](./egress-policy.md) -- where users may connect outside the cluster
(web, DNS, NTP and rate-limited SSH), client SSH defaults, inbound filtering, and
alerts on tunnels and exposed listeners (roles/egress_policy).

## Rebooting the cluster

`playbooks/utilities/reboot.yml` reboots the targeted hosts one at a time and waits for
each to come back. `-e maintenance_serial=N` reboots N at a time, and
`-e maintenance_reboot_timeout=N` sets how long a node has to return -- the default 1800s allows for
BIOS POST and memory training on a GPU server. A compute node Slurm still schedules on
is refused: drain it first, or use `playbooks/utilities/apt-upgrade.yml`, which drains,
reboots, checks and resumes (`-e maintenance_reboot_undrained=true` overrides, losing
the node's jobs).

Roles never reboot a node themselves. A change that needs one (a new driver or kernel
module, kernel options, firmware) writes `/var/run/reboot-required`; the OS update
metrics report it and apt-upgrade.yml performs it. On nodes nobody uses yet,
`-e maintenance_reboot_now=true` reboots at once instead.

Before rebooting, the play detaches every NFS mount. Without that, a node holding a
`hard` mount from a server rebooting alongside it blocks in uninterruptible sleep during
its own shutdown and never reaches the reboot; see
[Slurm and NFS](./slurm-nfs.md). Nodes carrying the `nfs_detach_on_shutdown` unit are
already protected on every reboot path, including `scontrol reboot`.

A node that comes back before its NFS server boots without that share, and nothing
retries it. `playbooks/utilities/nfs-mount.yml` mounts it once the server is up.

## When a node will not finish booting

Two failure modes have taken every node of a cluster off the network at once, and neither
announces itself as what it is. [Boot failures](../deepops/boot-failures.md) records what
each looks like on the console, why it happens, and how to tell them apart.

## Out-of-band management

A node whose OS has stopped -- hung in a shutdown, halted at a boot prompt -- answers
nothing on the cluster network, and Ansible cannot reach it. Its BMC can, so configuring
one before it is needed is what keeps a bad reboot from becoming a trip to the rack.

`playbooks/utilities/bmc.yml` configures each node's BMC from the node itself, and
`playbooks/utilities/power.yml` drives chassis power from the control node afterwards.
See [out-of-band management](../deepops/bmc.md).

## Utility playbooks

[Utility playbooks](../deepops/utilities.md) lists every playbook for running the cluster
after it is deployed -- power, reboots, NFS, GPU caps and the rest.
## Uninstalling Slurm

DeepOps has no automated Slurm uninstall playbook (the Kubernetes side has the Kubespray reset playbook, see [Reset the Cluster](../k8s-cluster/README.md#reset-the-cluster)). For the list of services, files, and system changes the Slurm playbook makes, and a manual procedure to undo them, see the [Slurm uninstall guide](./slurm-uninstall.md).
