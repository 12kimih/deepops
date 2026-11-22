# Job-scoped epilog cleanup

Starting with release 26.11, the Slurm epilog cleans up after a finished job
without touching other jobs of the same user that are running on the same node.
This page explains what changed, why, and how to go back to the old behaviour
for one release.

## What changed

When a job ends, the epilog (`/etc/slurm/epilog.d/*-lastuserjob-*`) removes what
the user left behind on the node: stray processes, files in `/tmp` and
`/dev/shm`, System V IPC objects and POSIX message queues, enroot containers,
and the user's SSH access entry.

Before 26.11 this was decided from the controller's view alone: the epilog asked
`squeue` whether the user had another job on the node, and if not it killed
every process the user owned (`killall -9 -u`) and deleted everything they owned
in `/tmp` and `/dev/shm`. Two things could go wrong:

- A new job of the same user could be placed on the node just after `squeue`
  answered, and have its processes killed a moment later.
- The controller can lag behind the node, and it cannot see processes that left
  the job's cgroup.

With `slurm_epilog_cleanup_scope: job` (the new default) the epilog also looks
at the node itself. Every process of a Slurm job lives in a `job_<id>` cgroup, so
the epilog reads `/proc/<pid>/cgroup` (cgroup v1 and v2 are both understood) and
decides per process:

| Process is in… | Epilog does |
|---|---|
| the ending job's cgroup | kills it (Slurm does too; this is a backstop) |
| another job's cgroup | never touches it, whatever `squeue` said |
| no job cgroup (an orphan, for example a leftover daemon) | kills it only when the user has no other job on the node |
| a cgroup entry it cannot read or recognise | leaves it alone and logs it |

The file cleanup (`/tmp`, `/dev/shm`), enroot directory removal, and SSH access
removal still happen per user, because files carry a user id and not a job id.
They now run only when neither `squeue` nor the node's cgroups show another job
of that user. If that check cannot be completed, the cleanup is skipped and
logged, then happens when the user's next job ends. A skipped cleanup is a delay,
not a loss.

In short: **less is deleted, never more.** Nothing that was spared before is
removed now.

### What you may notice

- If a user's job is still running when another of their jobs ends, that job's
  processes, `/tmp` files and enroot containers now survive. Previously they
  could be removed.
- After the user's last job ends, leftovers are still removed as before.
- `/tmp` cleanup can be delayed on a node where the cgroup data cannot be read.
  The epilog logs a `[WARN]` line (`slurm` tag in the journal, also in
  `/var/log/slurm/prolog-epilog`) when it defers.
- Any extra `*-lastuserjob-*` script you added under `/etc/slurm/epilog.d`
  follows the same, stricter guard and therefore runs less often.
- Accounts listed in `/etc/slurm/localusers.backup`, and root, are exempt as
  before.
- Requires cgroup-based process tracking (`ProctrackType=proctrack/cgroup`, the
  DeepOps default). With a different process tracker the epilog cannot tell
  which job a process belongs to, so it treats every process as unassigned and
  falls back to asking `squeue`: if the user has another job on the node nothing
  is cleaned up, otherwise all of the user's processes are removed. It does not
  defer. Use `user` (below) or fix the tracker if that is not what you want.
- Processes started outside Slurm while a job is finishing, for example over
  SSH, are not protected: they are not part of any job, so they count as
  unassigned and can be removed when the user's last job ends.

### Related fix: SSH access entry

The script that removes a user's SSH access entry (`41-lastuserjob-ssh`) matched
names as patterns rather than whole lines: ending a job for user `al` could also
remove the entries for `alice` or `halley`, and a dot in a name matched any
character. It now matches the exact line. This applies in both scopes.

## Going back to the 26.09 behaviour

If you need the old behaviour, set this in `config/group_vars/slurm-cluster.yml`
and re-run the Slurm playbook (`ansible-playbook playbooks/slurm-cluster.yml`) so the epilog scripts are re-rendered on the compute nodes:

```yaml
slurm_epilog_cleanup_scope: user
```

The scripts for `user` are the 26.09 scripts byte-for-byte (apart from the SSH
match fix above). The playbook prints a deprecation notice when `user` is set.

**`user` is deprecated and will be removed in release 27.01.** It exists for one
release so a site with an unusual setup, for example a custom epilog, a process
tracker other than cgroup, or user daemons that live outside any job cgroup, can
keep the old behaviour while it reports the gap. If you need `user`, please open
an issue at https://github.com/NVIDIA/deepops/issues saying why.

## Per-job `/tmp`, `/var/tmp` and `/dev/shm`

Each job gets its own empty `/tmp`, `/var/tmp` and `/dev/shm`, removed with
everything in them when the job ends, through Slurm's `namespace/linux` plugin.
The role writes `namespace.yaml` next to `slurm.conf` on the controller
(slurmctld serves it to every slurmd), names the plugin in `slurm.conf`
(`NamespaceType=namespace/linux`, with `Contain` in `PrologFlags`), and takes
both from one setting:

```yaml
slurm_job_private_dirs: [/tmp, /var/tmp, /dev/shm]   # [] turns it off
slurm_job_private_base: /var/spool/slurmd-private    # node-local disk
```

`/dev/shm` is always a fresh tmpfs; the other directories are bind mounts backed
by `slurm_job_private_base`, mounted `nodev,nosuid`. SSH sessions that
`pam_slurm_adopt` adopts into the job see the job's copies. The plugin needs
cgroup v2, which the role checks on every compute node. A site's own
`slurm_conf_template` must follow the setting too: the controller refuses a
template whose `NamespaceType` disagrees with it, or one that still uses the
deprecated `JobContainerType`.

When `/tmp` and `/dev/shm` are both per job, the epilog skips its own file
cleanup under them, which would reach only the host's copies. The enroot
prolog likewise leaves a path under a per-job directory (by default enroot's
data path, `/tmp/enroot-data/user-<uid>`) to enroot, which creates it inside
the job.

Changing either setting restarts slurmd on every node (`scontrol reconfigure`).
Deploy it while the nodes run no jobs: a running job's directories stay where
the old settings put them. See `namespace.yaml(5)` and
https://slurm.schedmd.com/namespace.yaml.html.

### A filesystem of their own (`--tmp`)

On a node whose only disk is the root filesystem, the base shares it with the OS,
slurmd, its logs and the container caches: one job that fills its `/tmp` fills
them all, fails the other jobs on the node and drains it. `slurm_enable_job_scratch:
true` (`roles/job_scratch`, on `slurm-node`, before Slurm in the deploy) gives the
base an XFS filesystem of its own on every compute node alike: an image
preallocated on the root filesystem (`job_scratch_file`, `job_scratch_size`, 70%
by default, keeping `job_scratch_reserve` free) and loop-mounted at the base
(`nodev,nosuid,noatime`, with `prjquota` so a limit per job needs no remount).
slurmd starts only with it mounted.

Slurm learns each node's size: `TmpFS` is the base, and each node's `TmpDisk` the
filesystem's size in whole GiB (a local fact read on every deploy), so a job asks
for space with `--tmp=500G` and runs only where that much exists. A job that asks
for none may still use what is free; the filesystem bounds them all together.

The first deploy needs the node idle: mounting over the base would hide the
directories of running jobs, and the role stops if it finds any. A larger
`job_scratch_size` later grows the filesystem in place (running jobs keep their
files); XFS cannot shrink, so a smaller one stops the role. Turning it off removes
the fstab entry, slurmd's ordering and the image; the mount and its space go at
the next boot.

## IPC objects

System V shared memory segments, semaphores and message queues, and POSIX
message queues (`/dev/mqueue`), are kernel objects, not files in a directory a
job could get its own of: they outlive the processes that made them, and an MPI
or NCCL job that crashes leaves them behind. systemd-logind removes a user's at
their last logout, but the role turns that off on Slurm nodes
(`RemoveIPC=no`, `slurm_fix_shm`), since a job is no login and a logout must
not pull them from a running job. `epilog.d/43-lastuserjob-ipc` removes them
instead when the user's last job on the node ends, as logind would. Accounts in
`localusers.backup` keep theirs, as they keep their processes. POSIX shared
memory and semaphores are files in `/dev/shm`, covered above.

## Enroot and Pyxis

The enroot cleanup removes the user's runtime and data directories when their
last job on the node ends. With a per-job `/tmp` (above), the default data path
is per job already, so a job's unpacked containers go with it. If you want containers that disappear with the job, Pyxis has a
container scope setting for job-lifetime containers; see the Pyxis documentation
for your version.

## Checking what the epilog did

Each epilog logs through `logger -t slurm-epilog` and `-t slurm`. Typical lines:

```
slurm-epilog: Job-scoped process cleanup for alice job 4711: killed 3, spared 12, unclassified 0
slurm: Another job of alice has processes on this node (found by cgroup); not running the last-user-job scripts.
```

## Testing

The unprivileged unit tests are in `tests/slurm-epilog/run-tests.sh` and run in CI.
They use a fake `/proc` and cover both cgroup layouts.
