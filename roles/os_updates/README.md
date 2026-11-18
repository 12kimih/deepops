# os_updates

Keeps unattended OS upgrades on cluster hosts to security fixes, away from the
packages that change the kernel, GPU, scheduler, container or storage stack, and
stops them from restarting services or rebooting. Those packages are upgraded in
a maintenance window by the roles that own them.

Left to their defaults, an unattended libc6 upgrade makes needrestart restart
nearly every service, nfs-server and slurmctld included, under running jobs; a
hung nfsd then takes every NFS client with it. With this role, needrestart only
lists the services that need a restart.

| Distribution | Mechanism |
|---|---|
| Ubuntu, Debian | unattended-upgrades: `/etc/apt/apt.conf.d/20auto-upgrades` and `52deepops-unattended-upgrades` (security origins, held-back packages, no reboot); needrestart `/etc/needrestart/conf.d/50-deepops.conf` |
| RHEL family | dnf-automatic: `upgrade_type = security`, `excludepkgs`, `reboot = never` |

The held-back list is written once, as globs; the apt template derives the
regular expressions unattended-upgrades needs (tests/os-updates checks that both
hold back the same packages).

## Variables

<!-- BEGIN GENERATED: role-variables:os_updates -->
| Variable | Default | Description |
|---|---|---|
| `os_updates_policy` | `security` | How hosts update packages on their own: `security` (security fixes only, never the held-back packages below, never a reboot), `off` (no unattended upgrades) or `unmanaged` (leave the distribution's settings as they are). |
| `os_updates_held_back` | `[linux-*, kernel*, systemd*, libsystemd*, udev, libudev*, libc6*, glibc*, dbus*, '*nvidia*', cuda*, libcudnn*, libnccl*, datacenter-gpu-manager*, slurm*, '*munge*', nfs-*, docker-ce*, containerd*, enroot*]` | Packages unattended upgrades never touch (shell-style globs on the package name). They change the kernel, GPU, scheduler, container or storage stack, or (systemd, libc, dbus) their upgrade re-executes PID 1 or restarts core daemons under running jobs; a daemon-reexec from an unattended upgrade can leave systemd hung. They are upgraded in a maintenance window, with a reboot. |
| `os_updates_restart_services` | `false` | Whether needrestart may restart services after an upgrade. When false it only lists them, so an upgrade never restarts nfs-server, slurmctld or similar under running jobs. |
<!-- END GENERATED: role-variables:os_updates -->

## Checking a host

```bash
apt-config dump | grep -E 'Origins-Pattern|Package-Blacklist|Automatic-Reboot'
sudo unattended-upgrade --dry-run --debug 2>&1 | grep -E 'Allowed origins|blacklist|Packages that will be upgraded'
cat /etc/needrestart/conf.d/50-deepops.conf
```
