# Uninstalling Slurm

How to remove a DeepOps Slurm deployment from a node, including the single-node
(login-on-compute) layout.

- [Uninstalling Slurm](#uninstalling-slurm)
  - [Before you start](#before-you-start)
  - [What the Slurm playbook puts on a node](#what-the-slurm-playbook-puts-on-a-node)
  - [Uninstall procedure](#uninstall-procedure)
    - [1. Stop and disable the services](#1-stop-and-disable-the-services)
    - [2. Remove the Slurm packages](#2-remove-the-slurm-packages)
    - [3. Remove configuration, state, and logs](#3-remove-configuration-state-and-logs)
    - [4. Undo the login and PAM changes](#4-undo-the-login-and-pam-changes)
    - [5. Undo the system tweaks](#5-undo-the-system-tweaks)
    - [6. Controller only: database and accounting](#6-controller-only-database-and-accounting)
    - [7. Optional components](#7-optional-components)
    - [8. Reboot and verify](#8-reboot-and-verify)
  - [Reinstalling instead of uninstalling](#reinstalling-instead-of-uninstalling)

## Before you start

DeepOps installs Slurm from source and makes a number of system-level changes
on each node. There is no "undo" playbook: unlike the Kubernetes deployment,
which has the Kubespray reset playbook (see
[Reset the Cluster](../k8s-cluster/README.md#reset-the-cluster)), removing
Slurm is a manual procedure. The cleanest result on a node that is going to
be repurposed is always a fresh OS install. Use the steps below when that is
not an option.

Everything here assumes the default variable values from
`roles/slurm/defaults/main.yml`. If your `config/group_vars/slurm-cluster.yml`
overrides any of the variables named in the tables, substitute your values.

This is a destructive checklist, not a script to paste wholesale. Obtain a
maintenance window, drain jobs, and back up configuration, accounting, and
state before proceeding. Keep console access available. Check each path for
mounts, symlinks, custom prefixes, and use by other clusters or applications.
Never delete shared storage as part of removing only one node.

Run commands as root (or with `sudo`) on the node being cleaned.
On a single-node deployment the controller and compute steps both apply to
the same machine. Retain backups until a fresh login and the remaining
applications have been verified.

## What the Slurm playbook puts on a node

Systemd units (controller units exist only on `slurm-master` hosts, `slurmd`
only on `slurm-node` hosts):

| Unit | Where it comes from |
|---|---|
| `slurmctld.service`, `slurmdbd.service` (and the drop-in `/etc/systemd/system/slurmdbd.service.d/`) | Packages `slurm-smd-slurmctld`, `slurm-smd-slurmdbd` (`/lib/systemd/system/`) |
| `slurmd.service` | Package `slurm-smd-slurmd` |
| `sackd.service` | Package `slurm-smd-sackd`, on hosts with neither `slurmd` nor `slurmctld` |
| `munge.service` | Distribution package `munge` |
| `mariadb.service` | Distribution package `mariadb-server` (controller only) |
| `docker.slurm-exporter.service`, `docker.node-exporter.service`, `docker.dcgm-exporter.service`, `docker.prometheus.service`, `docker.grafana.service`, `docker.alertmanager.service` | Only when `slurm_enable_monitoring` is set |

Files and directories (variable name in parentheses):

| Path | Purpose |
|---|---|
| The `slurm-smd*` packages | Commands in `/usr/bin` and `/usr/sbin`, plugins in `slurm_plugin_dir` (`/usr/lib/<multiarch>/slurm`), headers in `/usr/include/slurm` |
| `/etc/apt/sources.list.d/deepops-slurm.list`, `/etc/apt/keyrings/deepops-slurm.asc`, `/etc/apt/preferences.d/deepops-slurm` | The Slurm repository, its key and the version hold |
| `/sw/.slurm-repo` (`slurm_repo_dir`) | The signed package repository every node installs from (shared) |
| `/var/cache/deepops/build/slurm-deb` (`slurm_deb_build_dir`), user `slurmbuild` | The package build, on `slurm-master[0]` only |
| `/etc/slurm` (`slurm_config_dir`) | Controller: `slurm.conf`, `cgroup.conf`, `gres.conf`, `slurmdbd.conf`, `plugstack.conf`, `job_submit.lua`. Every node: `prolog.d/`, `epilog.d/`, `shared/bin/`, `localusers.backup`, Pyxis `plugstack.conf.d/` |
| `/run/slurm/conf` | The configuration `slurmd` or `sackd` fetched from `slurmctld` (configless) |
| `/sw/slurm` (`slurm_ha_state_save_location`) | State directory when `slurm_enable_ha` is set |
| `/var/spool/slurm/ctld`, `/var/spool/slurm/d` | Controller and compute state |
| `/var/log/slurm` | Daemon logs |
| `/etc/sysconfig/slurmd`, `/etc/sysconfig/sackd` (`slurm_sysconf_dir`) | `--conf-server` and the PMIx environment for `slurmd`; `--conf-server` for `sackd` |
| `/etc/rsyslog.d/99-slurm.conf` | Routes Slurm logs through rsyslog |
| `/etc/munge/munge.key` | Cluster-wide munge key |
| `/etc/localusers` | Users allowed to SSH without a job |
| `/local/slurm` (`slurm_user_home`) | Home of the `slurm` user (uid `slurm_user_uid`, default 22078) |
| `/usr/lib/<multiarch>/security/pam_slurm_adopt.so` (`slurm_pam_lib_dir`) | Package `slurm-smd-libpam-slurm-adopt` |

System changes outside those paths:

- `/etc/pam.d/sshd` gets an `ANSIBLE MANAGED BLOCK (ansible-role-slurm)` that
  loads `pam_slurm_adopt`. With `slurm_restrict_node_access` (the default),
  SSH to a compute node is refused for users without a running job and not
  listed in `/etc/localusers`.
- `pam_systemd.so` is commented out in `/etc/pam.d/common-session` (Ubuntu)
  or `/etc/pam.d/password-auth` (RHEL).
- `/etc/default/grub` gets `cgroup_enable=memory swapaccount=1` appended to
  `GRUB_CMDLINE_LINUX` on compute nodes.
- `/etc/systemd/logind.conf` gets `RemoveIPC=no` on compute nodes.
- With `slurm_login_on_compute` (single-node layout) the SSH service unit gets
  a persistent `DeviceAllow=/dev/nvidiactl` property, stored under
  `/etc/systemd/system.control/<ssh unit>.d/50-DeviceAllow.conf`, so regular
  SSH sessions do not see the GPUs.
- The `slurm` system user and the MariaDB database `slurm_acct_db` plus the DB
  user `slurm` (controller).

## Uninstall procedure

Drain and stop all jobs first if the cluster is still in use. On a multi-node
cluster, clean the compute nodes before the controller so `slurmctld` does
not try to keep them registered.

### 1. Stop and disable the services

```bash
systemctl disable --now slurmd slurmctld slurmdbd 2>/dev/null
systemctl disable --now docker.slurm-exporter docker.node-exporter \
  docker.dcgm-exporter docker.prometheus docker.grafana docker.alertmanager 2>/dev/null
# Only if no other workload uses this authentication service:
# systemctl disable --now munge
```

### 2. Remove the Slurm packages

**Complete [step 4](#4-undo-the-login-and-pam-changes) first**, while the PAM
module and configuration backups still exist. Verify a fresh SSH login before
removing any PAM module or Slurm configuration.

```bash
apt-get purge -y 'slurm-smd*'
rm -f /etc/apt/sources.list.d/deepops-slurm.list /etc/apt/keyrings/deepops-slurm.asc \
      /etc/apt/preferences.d/deepops-slurm
apt-get update
```

A Pyxis plugin built on the node stays in `slurm_plugin_dir` (dpkg does not own it):
remove `spank_pyxis.so` and `spank_pyxis.so.built-from` there. On `slurm-master[0]`,
also remove the package build (`/var/cache/deepops/build/slurm-deb`, the user
`slurmbuild`, the build dependencies package `slurm-smd-build-deps`).

A node that still has the older source build (`make install` under `/usr/local`, with
`/opt/deepops/{pmix,hwloc}`): the upgrade playbook removes it
([slurm-upgrade.md](slurm-upgrade.md)); by hand, `make uninstall` in
`/opt/deepops/build/slurm` and its `contribs/pam_slurm_adopt`, then
`/usr/local/lib/slurm`, `/usr/local/include/slurm`, `/opt/deepops/{pmix,hwloc}`, the
build trees under `/opt/deepops/build`, `/etc/ld.so.conf.d/slurm.conf` (then
`ldconfig`) and the unit copies in `/etc/systemd/system`.

### 3. Remove configuration, state, and logs

```bash
rm -rf /etc/systemd/system/slurmdbd.service.d /etc/systemd/system/slurmd.service.d
systemctl daemon-reload

# Local paths only; inspect mounts/symlinks and save backups first.
rm -rf /etc/slurm
rm -rf /var/spool/slurm /var/log/slurm
rm -f /etc/sysconfig/slurmd /etc/sysconfig/sackd
rm -f /etc/rsyslog.d/99-slurm.conf && systemctl restart rsyslog
```

`/sw/.slurm-repo` (`slurm_repo_dir`) and `/sw/slurm`
(`slurm_ha_state_save_location`) may be shared by the entire cluster. Preserve
them when removing one node. Remove them only once, after all controllers and
compute nodes are retired and a restorable backup has been made.

Stop and disable munge, then remove its package and key only if nothing else
needs it:

```bash
apt-get purge -y munge libmunge-dev   # Ubuntu
# dnf remove -y munge munge-devel munge-libs   # RHEL family
rm -rf /etc/munge
```

### 4. Undo the login and PAM changes

Do this from a console session or a second SSH session you keep open, and
test a fresh SSH login before closing it.

```bash
# Remove the pam_slurm_adopt block
sed -i '/# BEGIN ANSIBLE MANAGED BLOCK (ansible-role-slurm)/,/# END ANSIBLE MANAGED BLOCK (ansible-role-slurm)/d' /etc/pam.d/sshd

# Restore pam_systemd (Ubuntu)
sed -i 's/^# \(.*pam_systemd.so.*\) # ANSIBLE MANAGED (ansible-role-slurm)$/\1/' /etc/pam.d/common-session
# Restore pam_systemd (RHEL family)
# sed -i 's/^# \(.*pam_systemd.so.*\) # ANSIBLE MANAGED (ansible-role-slurm)$/\1/' /etc/pam.d/password-auth

# Inspect /etc/localusers for use by other PAM rules before removing it.
# /etc/slurm/localusers.backup is a DeepOps-generated list, not a pre-install backup.
# rm -f /etc/localusers
```

Single-node / `slurm_login_on_compute` layouts also hide the GPUs from SSH
sessions. Remove that property from the SSH unit (`ssh.service` on Ubuntu,
`sshd.service` on RHEL):

Inspect `systemctl cat ssh.service` (or `sshd.service`) and the file
`/etc/systemd/system.control/<ssh unit>.d/50-DeviceAllow.conf`. Back it up and
remove only the DeepOps `DeviceAllow=/dev/nvidiactl` setting; delete the file
only if it contains no other settings. Do **not** use `systemctl revert`,
which also removes unrelated local unit overrides. If the file has other
site-specific device rules, restore the site's intended policy instead.

Reload systemd and restart the SSH unit during the maintenance window, with
console access available. Check `systemctl show <ssh unit> -p DeviceAllow
-p DevicePolicy` and `nvidia-smi -L` from a new SSH session. If GPU access is
still restricted, inspect other device policy rather than clearing all
security restrictions.

### 5. Undo the system tweaks

Compute nodes only. Restore the pre-install values from backup; the commands
below illustrate the stock DeepOps additions, not a reason to replace a site's
existing IPC or kernel policy. On RHEL-family hosts, use the bootloader update
command appropriate for that OS and boot mode.

```bash
# Kernel command line (requires a reboot to take effect)
sed -i '/^GRUB_CMDLINE_LINUX="${GRUB_CMDLINE_LINUX} cgroup_enable=memory swapaccount=1"$/d' /etc/default/grub
update-grub                                   # Ubuntu
# grub2-mkconfig -o /boot/grub2/grub.cfg      # RHEL family

# systemd-logind IPC cleanup
sed -i 's/^RemoveIPC=no$/#RemoveIPC=yes/' /etc/systemd/logind.conf
```

Remove the Slurm service account last, after the daemons are gone:

```bash
userdel -r slurm
```

### 6. Controller only: database and accounting

The controller keeps Slurm accounting in MariaDB. For a full cluster teardown
only, take and verify a database backup before dropping the configured database
and DB user. Do not do this when removing a compute node or one HA controller.
The following assumes the default names; retain MariaDB and its data directory
if any other application uses them:

```bash
mysql -e "DROP DATABASE IF EXISTS slurm_acct_db; DROP USER IF EXISTS 'slurm'@'localhost';"
# Optional: remove the server
# apt-get purge -y mariadb-server && rm -rf /var/lib/mysql   # Ubuntu
# dnf remove -y mariadb-server && rm -rf /var/lib/mysql      # RHEL family
```

### 7. Optional components

The Slurm cluster playbook also installs these when their flags are enabled.
Each is independent of Slurm and can stay in place if it is still useful.

| Component | Flag | What to remove |
|---|---|---|
| Enroot and Pyxis | `slurm_install_enroot`, `slurm_install_pyxis` (default on) | Pyxis configuration is under `/etc/slurm` (`plugstack.conf`, `plugstack.conf.d/pyxis.conf`); the plugin is installed as `{{ slurm_plugin_dir }}/spank_pyxis.so` from the source tree in `/usr/local/src/pyxis-<version>` (`pyxis_src_dir`). Remove Enroot only if no other workloads use it; see the AppArmor policy cleanup below. |
| Node Health Check | `slurm_install_nhc` | `/etc/nhc`, `/etc/sysconfig/nhc`, `/usr/sbin/nhc*`, `/usr/libexec/nhc`, `/opt/deepops/build/nhc` |
| OpenMPI | `slurm_cluster_install_openmpi` (example inventory: off; unset: on) | Built into `/usr/local` from `/tmp/openmpi-build`; `cd` to that directory and `make uninstall` if it still exists |
| Lmod / software modules | `slurm_install_lmod` | See `roles/lmod/defaults/main.yml` for `sm_software_path` and `sm_module_path`; profile scripts in `/etc/profile.d/` |
| EasyBuild | `playbooks/slurm-cluster/easybuild-modules.yml` | On the software-tree host: `easybuild_prefix`, the venvs in `{{ sm_software_path }}/EasyBuild`, the modulefiles in `{{ sm_module_path }}/EasyBuild`, `/etc/xdg/easybuild.d/deepops.cfg`, and the `easybuild_user` account with its home. Drop the EasyBuild tree from `lmod_module_paths` before rerunning the Lmod role |
| Rootless Podman | `slurm_install_podman` (default on) | The `podman`, `uidmap` and `slirp4netns` packages; `/etc/subuid`, `/etc/subgid`, `/etc/containers/storage.conf`, `/etc/containers/containers.conf.d/50-deepops-rootless.conf`, `/etc/tmpfiles.d/podman-users.conf`, `/usr/local/bin/podman`; the users' storage under `podman_storage_root` (default `/var/lib/podman-storage`). |
| Monitoring | `slurm_enable_monitoring` | The listed monitoring units, including `docker.alertmanager.service` (stopped in step 1), their unit files in `/etc/systemd/system/`, and their matching containers. Inspect names and other users before removing containers; do not remove unrelated `docker.*` services. Run `systemctl daemon-reload` after removing unit files. Back up retained monitoring data; remove configuration and volumes only if no longer needed. |
| Centralized syslog | `slurm_enable_rsyslog_client` (default on) | `/etc/rsyslog.d/99-forward-syslog.conf` on compute nodes, then `systemctl restart rsyslog` |
| NFS | `slurm_enable_nfs_server`, `slurm_enable_nfs_client_nodes` | Exports and mounts configured by the NFS roles; see [Slurm and NFS](./slurm-nfs.md) |

When retiring Enroot, restore the site's intended AppArmor policy, not just
its files. The enroot package ships the scoped profile `/etc/apparmor.d/enroot`
and removing the package removes it. If `enroot_apparmor_global_disable` was
enabled, inspect `/etc/sysctl.d/90-enroot.conf`: it sets
`kernel.apparmor_restrict_unprivileged_userns=0` for the whole host. Remove only
that override, then restore and apply the site's intended value with the
distribution's sysctl tooling, and verify it with
`sysctl kernel.apparmor_restrict_unprivileged_userns` where the knob exists.
Deleting the file alone does not restore the running kernel value.

The NVIDIA driver, CUDA toolkit, container toolkit, Docker, chrony, and the
`/etc/hosts` and hostname changes made by the generic playbooks are not Slurm
specific and are left in place by this procedure.

### 8. Reboot and verify

Reboot the node so the kernel command line and `logind` changes apply, then
check:

```bash
systemctl list-units --all 'slurm*' 'munge*'   # no running Slurm units; munge may be retained
command -v srun sinfo slurmd || echo "slurm binaries gone"
grep -c slurm /etc/pam.d/sshd                   # expect 0
nvidia-smi -L                                    # GPUs visible from a normal SSH session
```

## Reinstalling instead of uninstalling

If the goal is to reinstall Slurm while keeping configuration, state and accounting,
you do not need the removal procedure above: `apt-get install --reinstall` the
`slurm-smd*` packages on the node, or rebuild them with `-e slurm_force_rebuild=true`
on `playbooks/slurm-cluster/slurm.yml` (same version, same package names).
