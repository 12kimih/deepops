# Docker access from inside Slurm jobs

Adding a user to the `docker` group on a compute node does **not** give that user
access to the Docker socket inside a Slurm job. This document explains why, and how
this repo solves it.

## The problem

Slurm resolves a job's supplementary groups on the **submit/controller** node and
ships the resulting **numeric** GID list into the job. This is the `send_gids`
behaviour, on by default since Slurm 20.11 and disabled only with
`LaunchParameters=disable_send_gids`. The compute node never re-resolves the names —
`slurmstepd` applies the numbers it was given and the node's own group database is
consulted only to render those numbers back into names.

Two consequences follow, and both are silent:

1. **Editing `/etc/group` on a compute node has no effect inside jobs.** The file is
   never read when the job's credentials are built, so `usermod -aG docker <user>` on
   the node changes nothing. Re-running `srun`, restarting the allocation, or
   re-logging in does not help, because the node was never the source.

2. **A GID that means different things on different nodes grants the wrong group.**
   Access does not fail closed. The number arrives, the compute node renders it through
   its own group database, and the job ends up holding whatever group happens to own
   that number there — no error, no warning.

The `docker` group is especially prone to this. It is created by the Docker package
with a distro-assigned **system** GID, which depends on the order packages were
installed on each node, so it routinely differs across a cluster.

NIS cannot paper over it either: `/var/yp/Makefile` filters the group map to
`GID >= MINGID`, taken from `GID_MIN` in `/etc/login.defs` (normally 1000). A system
group numbered below that is **dropped from the map without a warning**, so it can
never be published no matter how often the maps are rebuilt.

## The fix

Do not try to make the `docker` group consistent. Point dockerd at a **different**
group that is designed to be cluster-wide:

1. Declare the group once, with a pinned GID at or above `GID_MIN`, in
   `config/group_vars/all.yml`:

   ```yaml
   cluster_docker_group_name: dockerusers
   cluster_docker_group_gid: 4000
   ```

   Verify the GID is free on **every** node before choosing it:

   ```sh
   for h in <nodes>; do ssh $h "getent group 4000 >/dev/null || echo $h free"; done
   ```

2. Publish it from the NIS master (`config/group_vars/nis-master.yml`). The
   `nis_server` role creates the group, adds the members, and rebuilds the maps:

   ```yaml
   nis_export_groups:
     - name: "{{ cluster_docker_group_name }}"
       gid: "{{ cluster_docker_group_gid }}"
       members:
         - alice
   ```

   The role asserts each GID is `>= GID_MIN`. Membership is **declarative** — deleting
   a name here revokes that person's access on the next run. Set
   `nis_export_groups_exclusive: false` to make the list additive instead, for a group
   other tooling also writes to.

3. Chown the socket to it on every node (`config/group_vars/slurm-cluster.yml`):

   ```yaml
   docker_socket_group: "{{ cluster_docker_group_name }}"
   docker_socket_group_gid: "{{ cluster_docker_group_gid }}"
   ```

   The `docker_socket_group` role writes `group` into `/etc/docker/daemon.json`,
   creates the group locally with the same GID (so dockerd can resolve the name at
   boot even before the NIS client has bound), and keeps `docker.socket`'s
   `SocketGroup=` in agreement. Applying it **restarts dockerd**, which stops running
   containers on the node; set `docker_socket_group_restart: false` to stage the
   config and bounce the daemon during a maintenance window instead.

`playbooks/slurm-cluster.yml` runs this after `authentication.yml`, so the group is
published before the nodes are asked to resolve it. Both pieces are no-ops until
`docker_socket_group` and `nis_export_groups` are set.

## Verifying

```sh
ansible-playbook playbooks/utilities/check-id-consistency.yml
```

asserts the published groups resolve to their pinned GID on every node. Then, from a
**new** job (existing jobs and login sessions keep the credentials they were created
with):

```sh
srun --pty bash
id -nG            # must list the cluster group
docker ps
```

If `id -nG` still does not show it, check in this order: the maps were rebuilt on the
master (`ypcat group | grep <name>`), the node is bound (`ypwhich`), and no **local**
group of the same name shadows the NIS one — `nsswitch.conf` resolves `files` before
`nis`.

## Before reaching for this

Membership in the socket group is **root-equivalent on that node**: a container that
bind-mounts `/` escapes trivially. Publishing it cluster-wide makes that a standing
grant on every node.

Containers started through dockerd are also **outside the job's cgroup**. They are
children of the daemon, not of `slurmstepd`, so they survive `scancel` and the end of
the allocation, and their GPU, CPU and memory usage is not charged to the job.

Enroot and Pyxis have neither problem — they are rootless, scoped to the job, and
GPU-aware:

```sh
srun --container-image=ubuntu:22.04 nvidia-smi
```

Use `docker_socket_group` only for workloads Enroot genuinely cannot run, such as
multi-container `docker compose` topologies with a shared network.
