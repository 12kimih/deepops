# Per-user disk quotas

`roles/disk_quota` (switch `slurm_enable_disk_quota`, off by default) sets per-user
quotas on local ext4 filesystems: the login nodes' disks (`slurm-login`) and the home
server's `/home` (`slurm-nfs`). Which filesystems each host limits is
`disk_quota_filesystems` (default `["/"]`); set it per host where a host plays both
parts, e.g. `["/", "/home"]`.

## Who is limited

Everyone in the roster (`people.yml`), administrators too: their own accounts are users
like any other, and what they do with `sudo` is root's, which a quota never limits.
Root and the system accounts (the database, Slurm, the web server) are never limited.
Each person gets `disk_quota_limits`, or their own `disk_quota` from the roster over it:

```yaml
# group_vars: the site's limits (setquota sizes: K, M, G, T; inodes: k, m, g, t)
disk_quota_limits: {block_soft: 10G, block_hard: 20G, inode_soft: 200k, inode_hard: 500k}
# people.yml: one person's own
people:
  alice:
    disk_quota: {block_soft: 50G, block_hard: 100G}
```

Someone who leaves the roster loses their limits; their files stay. A user may stay over
a soft limit for `disk_quota_grace_days`, which is the site's one cleanup period
(`cluster_user_grace_days`, 14 by default); after it the soft limit acts like the hard one.

## Count first, then limit

With `disk_quota_limits: {}` (the default) quotas only count. A timer exports every
user's usage every 15 minutes:

| Metric | Labels |
|---|---|
| `disk_quota_used_bytes`, `disk_quota_used_inodes` | `filesystem`, `user` |
| `disk_quota_limit_bytes`, `disk_quota_limit_inodes` | `filesystem`, `user`, `kind` (soft, hard) |
| `disk_quota_last_success_timestamp_seconds` | |

Look at the distribution, then choose limits that leave the filesystem's other users
(the controller's database, the logs) a reserve: roughly, the space users may share
divided by how many could fill theirs at once. Alerts report when the usage stops being
reported, and who has been over a soft limit for a day.

## Mount options

`/etc/fstab` has one writer, `host_baseline_mount_options` (roles/host_baseline, which
also remounts). Each limited filesystem needs its quota options there; without them the
role stops, naming them:

| The filesystem | Options | How quotas start |
|---|---|---|
| has ext4's quota feature (`tune2fs -l <device>` lists `quota`) | `usrquota` | counted from the moment it is mounted |
| has not | `usrjquota=aquota.user,jqfmt=vfsv1` | `quotacheck` counts every file once (the role runs it), journaled quota files after |

A root filesystem keeps journaled quota files: the feature can only be added to an
unmounted filesystem. A data filesystem such as `/home` should get the feature, in a
maintenance window, since it needs no quota file and no `quotacheck` after a crash.

## Adding the quota feature to /home (maintenance window)

The filesystem must be unmounted, so nothing may use it: the compute nodes drained, no
user logged in on the server, and NFS stopped (an NFS server is never restarted under
load). `tune2fs` counts every inode, which takes a while on a large filesystem.

```bash
sudo systemctl stop nfs-server
sudo fuser -vm /home          # must list nothing
sudo umount /home
sudo tune2fs -O quota -Q usrquota <device>   # asks for e2fsck -f first if the filesystem needs it
sudo tune2fs -l <device> | grep -i features  # lists quota
sudo mount /home
sudo systemctl start nfs-server
```

Then add `usrquota` to `/home`'s entry in `host_baseline_mount_options`, `/home` to the
server's `disk_quota_filesystems`, and run `playbooks/generic/host-baseline.yml` and
`playbooks/generic/disk-quota.yml` for the server (`-l <host> --check --diff` first).

## Removing it

Turning the switch off (or a host leaving the groups) removes the limits, turns quotas
off and removes the quota file. Drop the options from `host_baseline_mount_options` too,
or the next boot turns quotas on again.
