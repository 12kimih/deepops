# Scratch allocations

Fast scratch space is handed out per request instead of as one directory per person:
each allocation is a directory of its own, `<mount>/<user>-<id>`, with a size limit and
an end date. `roles/scratch_alloc` installs the tool (`clustertools.scratch_alloc`, as
`/usr/local/sbin/scratch-alloc`) and a timer on the host whose filesystem holds the
scratch space (`[scratch-server]`, switch `slurm_enable_scratch_alloc`).

## How an allocation lives

| Stage | What happens |
|---|---|
| Created | The directory is made, the user's alone (0700), with a project quota (XFS or ext4) of the requested size. |
| Resized | The project quota changes; data stays. |
| Extended | The end date moves (from the end date, or from today once it passed); a frozen allocation gets its size back. |
| Ended | At the end date the sweep freezes it: its limit drops to what it holds, so it can be read and copied away but not grow. |
| Removed | `scratch_alloc_grace_days` after the end (the cluster's cleanup period, `cluster_user_grace_days`), or at once when released: a released allocation is moved aside immediately and deleted by the next sweep. |

The registry (`<deepops_state_dir>/scratch-alloc/registry.json`, written whole under a
lock) is the record of every allocation, so a request takes effect the moment it is
accepted. The registry and the allocations are data: turning the component off never
removes them.

## Limits

- `scratch_alloc_max_size` and `scratch_alloc_max_days` are hard caps the tool enforces
  whoever asks.
- `scratch_alloc_auto_size`, `scratch_alloc_auto_days` and `scratch_alloc_auto_extensions`
  are what a request gets without an administrator; the cluster bot reads them.

## The filesystem

The scratch filesystem is XFS or ext4 with project quotas, which the tool sets through
`clustertools.disk_quota` (the same functions as roles/disk_quota's projects). Add
`prjquota` to its options in `host_baseline_mount_options` (host_vars of the scratch
server); roles/host_baseline writes `/etc/fstab` and, since a remount cannot turn XFS
quotas on, asks for the reboot that applies them. ext4 also needs its `project` feature
(`tune2fs -O project`, unmounted). The role stops until the filesystem is mounted so.

## Commands (root on the scratch server)

```bash
scratch-alloc /etc/cluster-tools/scratch-alloc.json list [USER]
scratch-alloc /etc/cluster-tools/scratch-alloc.json create USER SIZE DAYS [REASON]
scratch-alloc /etc/cluster-tools/scratch-alloc.json resize ID SIZE
scratch-alloc /etc/cluster-tools/scratch-alloc.json extend ID DAYS
scratch-alloc /etc/cluster-tools/scratch-alloc.json release ID
```

Each prints the allocation as JSON. The timer runs `sweep` and `metrics` every
`scratch_alloc_sweep_interval`; `scratch_alloc_limit_bytes`, `scratch_alloc_used_bytes`
and `scratch_alloc_expires_timestamp_seconds` (labels `id`, `user`, `state`) go to
node_exporter.
