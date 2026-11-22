# Scratch space

`/scratch` is the cluster's fast NVMe space for experiments whose data needs high
bandwidth: training sets read every epoch, intermediate results, checkpoints in use. It
is not a backup and not an archive: data to keep belongs in `/home`.

## Every person's own directory

Each active person in the roster has `/scratch/<login>`, as they have `/home/<login>`:
`roles/scratch_space` (switch `slurm_enable_scratch_space`) makes them on the host whose
local filesystem holds the space (`[scratch-server]`), owned by the person, with the
home's mode as a ceiling (`scratch_space_mode`). The top directory is root's
(`scratch_space_root_mode`, 0755): only the personal directories live there. Nothing is
asked for and nothing ends: a new person's directory comes with the next deploy of the
roster, a former member's goes with their home (`people_purge_paths`,
`playbooks/utilities/people-purge.yml`).

## Each person's space: their user quota

What a person may hold on `/scratch` is their user quota on that filesystem, whatever
directory the files are in ([disk-quota.md](../deepops/disk-quota.md)): on the scratch
server, `disk_quota_filesystems` with `/scratch` and its `block_hard`, the file count
following it (`disk_quota_inodes_per_tib`), and each warning at
`cluster_user_warn_fraction` of a limit. For example, 5T per person and 3000000 files per
TiB:

```yaml
# host_vars/<scratch server>
disk_quota_filesystems:
  /scratch: {block_hard: 5T}       # 15m files, warnings at 90%
disk_quota_inodes_per_tib: 3000000
host_baseline_mount_options:
  /scratch: defaults,relatime,usrquota
```

The sum of everyone's limits may exceed the disk: the space stays usable because unused
files do not stay. `/scratch` is XFS, whose quota option takes effect only when it
mounts: [disk-quota.md](../deepops/disk-quota.md#xfs-scratch) gives the one-time
unmount.

## Unused files are cleaned up (planned)

Files nobody has read or written for `cluster_user_stale_days` (60 days) are removed
after a notice: their owner gets a DM with how many and how much, and has
`cluster_user_grace_days` (14 days) in which using a file keeps it. What would go, and
when, can be listed at any time. "Used" is the file's access time, so data a job only
reads stays; the filesystem is mounted `relatime`, which keeps access times at a cost of
one update a day. Refreshing access times only to escape the cleanup (touching
everything) is forbidden: the space is for data in use.
