# Docker access from inside Slurm jobs

Adding a user to the `docker` group on a compute node does **not** give that user
access to the Docker socket inside a Slurm job. This document explains why, and how
this repo solves it.

## The problem

Slurm resolves a job's supplementary groups on the **submit/controller** node and
ships the resulting **numeric** GID list into the job. This is the `send_gids`
behaviour, on by default since Slurm 20.11 and disabled only with
`LaunchParameters=disable_send_gids`. The compute node never re-resolves the names --
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
   that number there -- no error, no warning.

The `docker` group is especially prone to this. It is created by the Docker package
with a distro-assigned **system** GID, which depends on the order packages were
installed on each node, so it routinely differs across a cluster.

A directory cannot paper over it either: no directory publishes the OS's own range.
NIS's `/var/yp/Makefile` filters the group map to `GID >= MINGID`, taken from `GID_MIN`
in `/etc/login.defs` (normally 1000), so a system group numbered below that is
**dropped from the map without a warning**; the LDAP directory holds only the cluster
groups it is given, at 1000 or more.

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

2. Publish it through the directory (`directory_groups` in
   `config/group_vars/all.yml`). The directory server creates the group with its
   members (`nis_server` in the master's files and its maps, `ldap_server` as a
   `posixGroup` entry):

   ```yaml
   directory_groups:
     - name: "{{ cluster_docker_group_name }}"
       gid: "{{ cluster_docker_group_gid }}"
       members:
         - alice
   ```

   Each GID is checked to be 1000 or more and no person's UID. Membership is
   **declarative** -- deleting a name here revokes that person's access on the next
   run. Set `directory_groups_exclusive: false` to make the list additive instead, for
   a group other tooling also writes to. In practice the members are
   `people | people_with_role('docker')`, from the people roster.

3. Chown the socket to it on every node (`config/group_vars/slurm-cluster.yml`):

   ```yaml
   docker_socket_group: "{{ cluster_docker_group_name }}"
   docker_socket_group_gid: "{{ cluster_docker_group_gid }}"
   ```

   The `docker_socket_group` role writes `group` into `/etc/docker/daemon.json`,
   creates the group locally with the same GID (so dockerd can resolve the name at
   boot even before the directory answers), and drops a `SocketGroup=` override
   into `docker.socket`. Both are written because which one decides depends on how the
   daemon was started: under socket activation (`dockerd -H fd://`) systemd creates the
   socket and `SocketGroup=` wins, while a daemon started any other way creates and
   chowns the socket itself and only the `group` key applies.

   Applying the change **restarts `docker.socket` and then dockerd**, which stops
   running containers on the node. The socket unit needs a restart of its own:
   `systemctl daemon-reload` re-reads the unit but does not re-create a socket that is
   already listening, and restarting `docker.service` does not restart `docker.socket`,
   so on a socket-activated node the override would otherwise sit unused until the next
   reboot. Set `docker_daemon_restart: false` to stage the configuration and
   bounce both during a maintenance window instead.

   On every run the role also compares the group that owns the live socket against the
   configured one and restarts if they differ. Without that check the role would act
   only when a configuration file changed, so a node that was staged, or whose handlers
   never ran, would report `ok` forever while its socket kept the old group.

`playbooks/slurm-cluster.yml` runs this after `authentication.yml`, so the group is
published before the nodes are asked to resolve it. Both pieces are no-ops until
`docker_socket_group` and `directory_groups` are set.

## Verifying

```sh
ansible-playbook playbooks/utilities/check-id-consistency.yml
```

asserts the published groups resolve to their pinned GID on every node. Then, from a
**new** job:

```sh
srun --pty bash
id -nG            # must list the cluster group
docker ps
```

### Existing sessions do not pick up new membership

A process's supplementary groups are fixed when it is created and are never re-read.
Adding someone to the group therefore has no effect on anything already running:
existing jobs, existing login sessions, and -- most easily missed -- the shells of a
long-lived `tmux` or `screen` server, which inherit the credentials that server was
started with however recently the shell itself was opened. The same applies in reverse:
removing someone from the group does not close the sessions they already have.

The two forms of `id` tell the cases apart, because only the second re-resolves the
name through the name service:

```sh
id -nG            # the running process's own credentials
id -nG <user>     # <user> looked up fresh
```

If they disagree, the configuration is fine and the session merely predates the change.
Log out and back in, or pick up the new list in place:

```sh
newgrp <group>    # nested shell with the group list re-resolved
```

### If the group is missing from `id -nG <user>` too

Check in this order: the directory has it (NIS: `ypcat group | grep <name>`; LDAP:
`getent -s sss group <name>`, after `sss_cache -E` if it changed in the last minutes),
the node reaches its directory (`ypwhich`, or `sssctl domain-status cluster`), and no
**local** group of the same name shadows the directory's -- `nsswitch.conf` resolves
`files` first, so `getent group <name>` on a client can report an empty member list
while the directory is correct.

### If the group is present but `docker ps` is still refused

Look at the socket rather than the group:

```sh
stat -c '%G %a' /run/docker.sock    # must be the cluster group, mode 660
```

A wrong group here means the daemon was never restarted after the configuration was
written. Re-running the playbook with `docker_daemon_restart: true` repairs it --
the role checks the live socket, not just the configuration files.

## Before reaching for this

Membership in the socket group is **root-equivalent on that node**: a container that
bind-mounts `/` escapes trivially. Publishing it cluster-wide makes that a standing
grant on every node.

Containers started through dockerd are also **outside the job's cgroup**. They are
children of the daemon, not of `slurmstepd`, so they survive `scancel` and the end of
the allocation, and their GPU, CPU and memory usage is not charged to the job.

Enroot and Pyxis have neither problem -- they are rootless, scoped to the job, and
GPU-aware:

```sh
srun --container-image=ubuntu:22.04 nvidia-smi
```

Use `docker_socket_group` only for workloads Enroot genuinely cannot run, such as
multi-container `docker compose` topologies with a shared network.
