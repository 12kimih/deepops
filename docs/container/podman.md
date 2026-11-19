# Building container images with rootless Podman

Jobs run containers with Enroot and Pyxis (`srun --container-image=...`). Enroot
imports images but cannot build one from a Dockerfile. For that, the compute nodes
have rootless [Podman](https://podman.io/): build the image inside a CPU job, then
import it into Enroot as a squashfs file and run that file with Pyxis.

- [Build and import](#build-and-import)
- [In a batch job](#in-a-batch-job)
- [Where images live](#where-images-live)
- [What does not work](#what-does-not-work)
- [Troubleshooting](#troubleshooting)
- [For administrators](#for-administrators)

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

## What does not work

- Long-running containers or services. Every process, the containers included,
  belongs to the job and ends with it. There is no `podman system service`,
  `podman generate systemd` or Quadlet: users have no systemd instance on the nodes.
- GPUs in `podman run`. Run GPU work with Pyxis or Enroot from the imported image.
- Building or running outside a job: compute nodes are reached through Slurm.

## Troubleshooting

`mkdir /var/lib/podman-storage/<UID>: permission denied` or
`no subuid ranges found for user`
: Your account has no subordinate ID range on the node. Ask an administrator to
  add you; ranges come from the user roster.

Errors about `XDG_RUNTIME_DIR`, `/run/user/<UID>` or `pause.pid`
: Run `podman` from `PATH` (`/usr/local/bin/podman`), not `/usr/bin/podman`. The
  first points Podman at a runtime directory that exists on the node; a job
  otherwise inherits the login node's, which does not.

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
  `/usr/local/bin/podman` sets `XDG_RUNTIME_DIR` to `<podman_runtime_root>/<UID>`,
  which the role creates for each person and `/etc/tmpfiles.d/podman-users.conf`
  recreates at boot. `enroot import podman://` calls `podman` from `PATH` and so
  goes through it.
- **AppArmor.** Ubuntu 24.04 restricts unprivileged user namespaces. The apparmor
  package's `podman`, `crun` and `slirp4netns` profiles grant them to those programs
  only; the role fails if they are missing and loads any that is not loaded.

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
