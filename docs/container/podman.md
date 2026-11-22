# Building container images with rootless Podman

Jobs run containers with Enroot and Pyxis (`srun --container-image=...`). Enroot
imports images but cannot build one from a Dockerfile. For that, the compute nodes
have rootless [Podman](https://podman.io/): build the image inside a CPU job, then
import it into Enroot as a squashfs file and run that file with Pyxis.

- [Coming from Docker](#coming-from-docker)
- [Build and import](#build-and-import)
- [In a batch job](#in-a-batch-job)
- [Where images live](#where-images-live)
- [What does not work](#what-does-not-work)
- [Troubleshooting](#troubleshooting)
- [For administrators](#for-administrators)

## Coming from Docker

Containers run inside Slurm jobs, so the job's GPUs, CPUs, memory and cleanup apply to
them. The Docker daemon on a compute node serves the cluster's own services; using it
directly is an administrator's exception (the roster's `docker` role, which is root on
the node), and a container it runs past the cleanup grace period raises an alert.

| With Docker | On the cluster |
|---|---|
| `docker run --gpus 1 IMAGE CMD` | `srun --gpus=1 --container-image=IMAGE CMD` (Pyxis) |
| `docker run -v /data:/data IMAGE` | `srun --container-image=IMAGE --container-mounts=/data:/data CMD` |
| `docker build -t NAME .` | `podman build -t localhost/NAME .` in a CPU job (below) |
| `docker save` / a local image for Pyxis | `enroot import -o NAME.sqsh podman://localhost/NAME` |
| `docker push` | `podman push localhost/NAME REGISTRY/NAME` |

## Build and import

Building needs CPUs and memory, not a GPU. Ask for a CPU job; add your site's
partition or QoS options as usual.

```bash
srun --cpus-per-task=8 --mem=16G --time=01:00:00 --pty bash

cd ~/myimage                        # the directory with your Dockerfile
podman build -t localhost/myimage:1 .
mkdir -p ~/images
enroot import -o ~/images/myimage.sqsh podman://localhost/myimage:1
podman container prune -f           # enroot import leaves a stopped container behind
exit
```

`podman build` reads a Dockerfile (or Containerfile) and takes the same options as
`docker build`. The `.sqsh` file in your home directory is usable on every node:

```bash
srun --gpus=1 --container-image=$HOME/images/myimage.sqsh nvidia-smi
```

Inside the build, `RUN` steps run as root of the image, but that root is your own
user outside: the build cannot read or change anything you could not.

## In a batch job

```bash
#!/bin/bash
#SBATCH --job-name=build-myimage
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=01:00:00
set -euo pipefail

cd ~/myimage
podman build -t localhost/myimage:1 .
rm -f ~/images/myimage.sqsh         # enroot import refuses to overwrite a file
enroot import -o ~/images/myimage.sqsh podman://localhost/myimage:1
podman container prune -f
```

## Where images live

Podman keeps images and build layers on the node's local disk, in a directory of
your own (`/var/lib/podman-storage/<your UID>` by default), not in your NFS home.
That makes builds fast and repeat builds reuse cached layers, with two consequences:

- The Podman image exists only on the node that built it. Use the `.sqsh` file (or
  `podman push` to a registry) to get it anywhere else.
- The space is shared with everyone on the node. Remove what you no longer need:

  ```bash
  podman images                     # what is stored on this node
  podman system prune -a -f         # remove all unused images and build cache
  podman system reset -f            # remove everything of yours on this node
  ```

- A daily cleanup removes your whole store on a node when you have not used it
  there for 14 days, when you leave the cluster, or, least recently used first,
  when the disk is nearly full. It never touches it while you run podman. The
  enroot layer cache (`/var/lib/enroot-cache/user-<UID>`) is cleaned the same way.

## GPUs

Running GPU work from the imported image with Pyxis is the usual way. To test an
image with podman in a GPU job, request the GPUs by their CDI name:

```bash
srun --gpus=1 --pty podman run --rm --device nvidia.com/gpu=all localhost/myimage:1 nvidia-smi
```

The container stays in the job's cgroup, so it reaches only the job's GPUs, as
`nvidia-smi` in the job itself does.

## What does not work

- Long-running containers or services. Every process, the containers included,
  belongs to the job and ends with it. There is no `podman system service`,
  `podman generate systemd` or Quadlet: users have no systemd instance on the nodes.
- Building or running outside a job: compute nodes are reached through Slurm.

## Troubleshooting

`mkdir /var/lib/podman-storage/<UID>: permission denied` or
`no subuid ranges found for user`
: Your account has no subordinate ID range on the node. Ask an administrator to
  add you; ranges come from the user roster.

Errors about `XDG_RUNTIME_DIR`, `/run/user/<UID>` or `pause.pid`
: Run podman inside a job. Slurm sets `XDG_RUNTIME_DIR` to a directory of yours on
  the node for every job step; a shell that reached the node another way keeps the
  login node's, which does not exist there.

`overlay is not supported over nfs` or a very slow build
: You have your own `~/.config/containers/storage.conf`, which replaces the node's
  and probably puts storage in your NFS home. Remove it.

Anything else after a configuration change (`podman system migrate` is suggested)
: Run `podman system migrate`; if that does not help, `podman system reset -f`
  (deletes your images on that node).

## For administrators

`playbooks/slurm-cluster.yml` installs rootless Podman on `slurm-node` hosts with
`roles/podman` when `slurm_install_podman` is true (the default). It follows the
[rootless tutorial](https://github.com/containers/podman/blob/main/docs/tutorials/rootless_tutorial.md)
with the distribution packages (`podman`, `uidmap`, `slirp4netns`; podman 4.9 on
Ubuntu 24.04):

- **Subordinate IDs.** `/etc/subuid` and `/etc/subgid` are owned by the role and list
  one range per active person in `podman_people` (normally the people roster:
  `podman_people: "{{ people }}"`). A range is computed from the UID alone, starting
  at `podman_subid_base + (uid - 1000) * podman_subid_count`, so every node writes
  the same files and nobody's range moves when the roster changes. The deploy fails
  if the base does not exceed every roster UID or a range would pass the 32-bit ID
  space. Entries added by hand or by `useradd` are removed on the next run.
- **Storage.** `/etc/containers/storage.conf` sets the overlay driver and
  `rootless_storage_path = "<podman_storage_root>/$UID"`. The role creates each
  person's directory, owned by them and mode 0700, so no user can claim another's
  name first. Kernel 6.8 supports native overlay in a user namespace, so
  fuse-overlayfs is not needed. The storage root must be on local disk.
- **No systemd user instance.** Users have none on the nodes, so
  `/etc/containers/containers.conf.d/50-deepops-rootless.conf` sets
  `cgroup_manager = "cgroupfs"`, `events_logger = "file"` and the `k8s-file` log
  driver. With cgroupfs, rootless containers stay in the job's cgroup, so Slurm
  limits, accounting and cleanup apply to them; no cgroup delegation is needed.
- **Runtime directory.** A job inherits `XDG_RUNTIME_DIR=/run/user/<UID>` from the
  login node, which does not exist on the compute node, and podman fails on it.
  Slurm's job environment sets it instead (roles/slurm, see
  [the job environment](../slurm-cluster/job-environment.md)): the prolog creates
  `<slurm_user_runtime_root>/<UID>`, the task prolog exports it to every task of
  every step, and the epilog of the user's last job on the node removes it.
- **GPUs.** `roles/nvidia_container_toolkit` keeps the toolkit's CDI specification
  (`/var/run/cdi/nvidia.yaml`, refreshed by `nvidia-cdi-refresh` on driver changes
  and at boot, and by `playbooks/nvidia-software/nvidia-mig.yml` after a MIG change),
  which `--device nvidia.com/gpu=...` reads
  ([CDI support](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html)).
- **Cleanup.** `roles/container_storage_gc` (`slurm_enable_container_storage_gc`) runs
  `container-storage-gc` daily on every Slurm host. It reads the per-user store paths
  from storage.conf and enroot.conf, and removes a user's whole store when they left
  `container_storage_gc_people`, after `container_storage_gc_unused_days` without a
  change, or least recently used first while the filesystem is above
  `container_storage_gc_max_used_percent`, never while that user runs the tool. A
  podman store is recreated empty for its user, unless they left. Its metrics feed the
  `ContainerStorageCleanupStale` alert; the disk itself is covered by the existing
  filesystem alerts. `container-storage-gc /etc/cluster-tools/container-storage-gc.json
  --dry-run` lists what it would remove for leaving or being unused.
- **AppArmor.** Ubuntu 24.04 restricts unprivileged user namespaces. The apparmor
  package's `podman`, `crun` and `slirp4netns` profiles grant them to those programs
  only; the role fails if they are missing and loads any that is not loaded.

- **Containers outside Slurm.** Every container deepops starts carries the label
  `deepops.unit` (`deepops_container_label`, set by `roles/container_service`). The
  daily cleanup counts the node's Docker containers without it, running and stopped,
  and those created more than `container_storage_gc_unused_days` ago
  (`container_storage_gc_docker_unmanaged`, `container_storage_gc_docker_unmanaged_stale`);
  it never touches them. A running one past that age raises `ContainerOutsideSlurm`.
  `docker-prune.timer` (`roles/docker`, `docker_prune_after`) removes stopped
  containers, unused images and build cache by age. BuildKit's own garbage
  collection (daemon.json `builder.gc`, capped at `docker_build_cache_max`) runs
  between, but by size only, and it undercounts on overlay2.

Changing `podman_subid_base` or `podman_subid_count` moves every range; users then
need `podman system migrate` (or `podman system reset -f`) on each node.

### Replacing docker-rootless

The docker-rootless role is retired: it piped an unpinned installer from the network
into a root shell. `rootlessdocker_install_dir` is a retired setting, and a deploy
that still sets it stops. The first play of `playbooks/container/podman.yml` runs on
every `slurm-cluster` host, whatever `slurm_install_podman` says, and removes what
the role installed: its `/etc/profile.d` scripts, `/etc/sysctl.d/51-rootless.conf`,
`/etc/modules-load.d/br_netfilter.conf`, and the install tree
(`<sm_software_path>/rootless-docker`). The tree sits on the shared software
filesystem, which NFS clients cannot remove as root, so only the host where it is
local (the NFS server) removes it. Users' old daemon state under
`/var/tmp/docker-container-storage-<UID>` and `/var/tmp/xdg_runtime_dir_<UID>` is
left to them.
