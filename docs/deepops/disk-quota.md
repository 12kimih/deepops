# Disk quotas

`roles/disk_quota` (switch `slurm_enable_disk_quota`, off by default) sets per-user
quotas on local ext4 filesystems, the login nodes' disks (`slurm-login`) and the home
server's `/home` (`slurm-nfs`), and per-directory (project) quotas, a whole tree's
total, such as shared datasets. Which filesystems each host limits per user is
`disk_quota_filesystems` (default `["/"]`); set it per host where a host plays both
parts, e.g. `["/", "/home"]`.

## Two levels: warn and hard

| Level | Settings | What happens past it |
|---|---|---|
| warn | `block_warn`, `inode_warn` | Nothing is refused. The level is exported with the usage; an alert names who is past it, and the people's notices (Slack) read it. |
| hard | `block_hard`, `inode_hard` | The kernel refuses the write at once. The only limit enforced. |

The warn level is never set in the kernel: its soft limit stays 0, so there is no grace
period that turns into a refusal days later. Choose the hard limit to protect the
filesystem, and the warn level (say 75 to 80% of it) to tell people in time.

## Who is limited

Everyone in the roster (`people.yml`), administrators too: their own accounts are users
like any other, and what they do with `sudo` is root's, which a quota never limits.
Root and the system accounts (the database, Slurm, the web server) are never limited.
Each person gets `disk_quota_limits`, or their own `disk_quota` from the roster over it:

```yaml
# group_vars: the site's limits (setquota sizes: K, M, G, T; inodes: k, m, g, t)
disk_quota_limits: {block_warn: 15G, block_hard: 20G, inode_warn: 400k, inode_hard: 500k}
# people.yml: one person's own
people:
  alice:
    disk_quota: {block_warn: 80G, block_hard: 100G}
```

Someone who leaves the roster loses their limits; their files stay.

## Project quotas

`disk_quota_projects` gives a directory tree one limit, whoever writes in it:

```yaml
disk_quota_projects:
  /home/shared/datasets: {id: 101, block_warn: 15T, block_hard: 20T}
```

Each id is unique on its filesystem. The tree is marked with it (new files inherit it);
a file moved in from elsewhere on the filesystem is copied, so it is counted too. When a
project goes, only its limit is lifted: the tree and its data stay. Tools that make
projects at run time (scratch allocations) use the same functions
(`clustertools.disk_quota`: `require_project_quota`, `project_set`, `project_limit`,
`project_used`), on ext4 or XFS.

## Count first, then limit

With `disk_quota_limits: {}` (the default) quotas only count. A timer exports the usage
every 15 minutes:

| Metric | Labels |
|---|---|
| `disk_quota_used_bytes`, `disk_quota_used_inodes` | `filesystem`, `user` |
| `disk_quota_limit_bytes`, `disk_quota_limit_inodes` | `filesystem`, `user`, `kind` (warn, hard) |
| `disk_quota_project_used_bytes` | `filesystem`, `project`, `project_id` |
| `disk_quota_project_limit_bytes` | `filesystem`, `project`, `project_id`, `kind` |
| `disk_quota_last_success_timestamp_seconds` | |

The usage report's daily disk report (`slurm_usage_report_storage_schedule`,
[slurm-monitor.md](../slurm-cluster/slurm-monitor.md#slack-usage-report)) posts the same
figures each morning: every counted filesystem, its largest people and their day's growth,
the projects, and everyone past a warn level, so the people concerned hear about it.

Look at the distribution, then choose hard limits that leave the filesystem's other
users (the controller's database, the logs) a reserve: roughly, the space users may
share divided by how many could fill theirs at once. Alerts report when the usage stops
being reported, and who or which project has been past its warn level for an hour.

## No new Slurm jobs at a hard limit

With `slurm_enable_submit_holds` (off by default; `roles/submit_holds`, on the controllers)
someone at a hard limit, of space or of files, on any counted filesystem cannot submit
Slurm jobs until they free space there. `sbatch`, `srun` and `salloc` refuse the job
with the reason (where, how much, `quota -s` to look); their running jobs go on.

| Setting | What it does |
|---|---|
| `slurm_enable_submit_holds` | Turns the holds on (and the disk report says what a hard limit means for Slurm) |
| `submit_holds_from` | `YYYY-MM-DD`: no one is held before this date, the grace for everyone already at a limit when the holds are turned on; the disk report and its DMs say from when. `""` holds at once |
| `submit_holds_interval` | How often the holds are worked out again (5 minutes) |

A timer on the controllers reads the disk quota metrics from Prometheus (the same
figures and the same "at a hard limit" as the daily disk report) and writes
`/etc/slurm/submit-holds`; the rendered `job_submit.lua` reads that file at most once a
minute. A file not refreshed for half an hour, a missing one or one that cannot be read
holds no one, so a monitoring outage never stops everyone's jobs nor keeps someone held
who has freed space; `SubmitHoldsStale` warns after 15 minutes without a run. A site's
own `job_submit.lua` (not the role's template) does not read the holds.

Turning them on: set the switch and the date, run
`playbooks/slurm-cluster/submit-holds.yml`, then `playbooks/slurm-cluster/slurm.yml` so
the controllers' `job_submit.lua` reads the file (`--check --diff` first, both).
Announce the date before it comes.

## Mount options

`/etc/fstab` has one writer, `host_baseline_mount_options` (roles/host_baseline, which
also remounts). Each limited filesystem needs its quota options there; without them the
role stops, naming them:

| The filesystem | Options | How quotas start |
|---|---|---|
| ext4 with the quota feature (`tune2fs -l <device>` lists `quota`) | `usrquota` | counted from the moment it is mounted |
| ext4 without it | `usrjquota=aquota.user,jqfmt=vfsv1` | `quotacheck` counts every file once (the role runs it), journaled quota files after |
| ext4 with the quota and project features, for projects | `prjquota` (with `usrquota`) | counted from the moment it is mounted |
| XFS, for projects | `prjquota` | at mount time only (host_baseline asks for a reboot) |

A root filesystem keeps journaled quota files: the features can only be added to an
unmounted filesystem. A data filesystem such as `/home` should get them, in a
maintenance window, since it then needs no quota file and no `quotacheck` after a crash.
The project feature needs inodes of at least 256 bytes (`tune2fs -l` shows Inode size).

## Adding the quota features to /home (maintenance window)

The filesystem must be unmounted, so nothing may use it: the compute nodes drained, no
user logged in on the server, and NFS stopped (an NFS server is never restarted under
load). `tune2fs` counts every inode, which takes a while on a large filesystem.

```bash
sudo systemctl stop nfs-server
sudo fuser -vm /home          # must list nothing
sudo umount /home
sudo tune2fs -O quota,project -Q usrquota,prjquota <device>   # asks for e2fsck -f first if needed
sudo tune2fs -l <device> | grep -i features                   # lists quota and project
sudo mount /home
sudo systemctl start nfs-server
```

Then add `usrquota,prjquota` to `/home`'s entry in `host_baseline_mount_options`, `/home`
to the server's `disk_quota_filesystems`, its projects to `disk_quota_projects`, and run
`playbooks/generic/host-baseline.yml` and `playbooks/generic/disk-quota.yml` for the
server (`-l <host> --check --diff` first).

## Removing it

Turning the switch off (or a host leaving the groups) lifts the users' and the projects'
limits, turns user quotas off and removes the quota file. Drop the options from
`host_baseline_mount_options` too, or the next boot turns quotas on again.
