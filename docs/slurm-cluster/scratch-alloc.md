# Scratch allocations

Fast scratch space is asked for, one allocation per person: `<mount>/<login>`
(`/scratch/alice`), as `/home/<login>` is a home, with a size limit and an end date.
`roles/scratch_alloc` installs the tool (`cluster_tools.scratch_alloc`, as
`/usr/local/sbin/scratch-alloc`) and a timer on the host whose filesystem holds the
scratch space (`[scratch-server]`, switch `slurm_enable_scratch_alloc`); people ask with
the cluster bot's `/scratch`.

## What a request gets

One table, every period counted from the day the allocation was made:

| Size | Period | Decided |
|---|---|---|
| up to `scratch_alloc_auto_size` (5TB) | up to `scratch_alloc_auto_days` (90 days) | at once |
| up to `scratch_alloc_max_size` (10TB) | up to `scratch_alloc_max_days` (180 days) | an administrator approves |
| past those | | nobody (the tool refuses) |

A new allocation, a larger size and an extension all go by it; an extension adds at most
`scratch_alloc_max_extend_days` (90) at a time. Giving space back (release, a smaller
size) is always at once. While the scratch disk is `scratch_alloc_guard` (80%) full or
more, anything that grows waits for an administrator. Nothing is ever shrunk by the
system: the daily disk report shows each allocation, its use and days left.

## Who has held how much: the ledger

Every sweep adds to each person's ledger the bytes allocated and the bytes used, times
the time since the last sweep. The totals run from a person's first allocation and stay
after an allocation goes: TB-days, allocated and used. A request that waits for an
administrator says the requester's (cumulative allocated and used TB-days, and what they
hold now), and an administrator's `/scratch` lists what waits with the people who have
used least first: when the disk is full and many ask, that is the fairness to decide by.
The daily disk report shows the same totals.

## How an allocation lives

| Stage | What happens |
|---|---|
| Created | `<mount>/<login>` is made, theirs alone (0700), or taken as it is when it is already their own directory (files and all); its project quota (ID: the person's UID) is the size. |
| Resized | The project quota changes; data stays. |
| Extended | The end date moves (from the end date, or from today once it passed); a frozen allocation gets its size back. |
| Ended | At the end date the sweep freezes it: its limit drops to what it holds, so it can be read and copied away but not grow. |
| Removed | `scratch_alloc_grace_days` after the end (the cluster's cleanup period, `cluster_user_grace_days`), or at once when released: a released allocation is moved aside immediately and deleted by the next sweep. |

The registry (`/var/lib/scratch-alloc/registry.json`, written whole under a lock) records
each allocation, so a request takes effect the moment it is accepted. The registry and
the allocations are data: turning the component off never removes them.

## The filesystem

The scratch filesystem is XFS or ext4 with project quotas, which the tool sets through
`cluster_tools.disk_quota` (the same functions as roles/disk_quota's projects). Add
`prjquota` to its options in `host_baseline_mount_options` (host_vars of the scratch
server); roles/host_baseline writes `/etc/fstab` and, since a remount cannot turn XFS
quotas on, asks for the reboot that applies them. ext4 also needs its `project` feature
(`tune2fs -O project`, unmounted). The role stops until the filesystem is mounted so.

## Commands (root on the scratch server)

```bash
scratch-alloc /etc/cluster-tools/scratch-alloc.json list [LOGIN]
scratch-alloc /etc/cluster-tools/scratch-alloc.json create LOGIN SIZE DAYS [REASON]
scratch-alloc /etc/cluster-tools/scratch-alloc.json resize LOGIN SIZE
scratch-alloc /etc/cluster-tools/scratch-alloc.json extend LOGIN DAYS
scratch-alloc /etc/cluster-tools/scratch-alloc.json release LOGIN
```

Each prints the allocation as JSON (`list`: the allocations and the disk). The timer runs
`sweep` and `metrics` every `scratch_alloc_sweep_interval`: `scratch_alloc_limit_bytes`,
`scratch_alloc_used_bytes` and `scratch_alloc_expires_timestamp_seconds` (labels `user`,
`state`), the disk's `scratch_alloc_disk_bytes` and `scratch_alloc_disk_used_bytes`, and
each person's ledger, `scratch_alloc_allocated_byte_seconds_total` and
`scratch_alloc_used_byte_seconds_total` (label `user`), go to node_exporter.
