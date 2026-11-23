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

## Unused files are cleaned up

A daily timer on the scratch server (`scratch-space-purge.timer`, `roles/scratch_space`,
`scratch_space_purge`) looks at every file in each person's directory and in the
collaboration folders (`/scratch/projects`, each file counted to its owner):

1. A file nobody has used, read or written, for `cluster_user_stale_days` (60 days) is
   listed for its owner, with its removal date: `cluster_user_grace_days` (14 days) after
   it was first listed. The date stays the same on the next runs.
2. Using a listed file in the meantime (a job reading it is enough) takes it off the list
   on the next run. Nothing else keeps a file: there is no button to press.
3. On its date, a file still unused is removed, and the directories that leaves empty.
   A person's own directory and a collaboration folder itself always stay.
4. Unless its owner has a job holding its allocation then (running, still starting,
   suspended or completing), or, in a collaboration folder, any member of its group
   does: the file is deferred once, by `cluster_user_stale_days` more, and says so in
   the list and the notice (a folder whose group cannot be read counts as busy). On that
   date it goes even if jobs still run. When Slurm cannot be read, nothing is removed
   that day.
5. Only the file that was listed goes: the removal reaches it without following a link
   and checks it is still the same file (device, inode and owner), so a directory
   swapped for a link, or a file replaced since the scan, is left alone.

"Used" is the later of the file's access and modification times, so data a job only
reads stays; the filesystem is mounted `relatime`, which keeps access times at the cost
of one update a day (under `noatime`, a file only read would look unused). Over NFS
that is not the whole story: a node that opens an unchanged file it has read before
checks its attributes and reads its own cached copy, so the server never sees the read.
A job re-reading the same small files (code, a config, an index) for weeks could look
unused, which is what the running-job deferral (4) covers. The cleanup follows no
symbolic link and enters no other filesystem.

### Seeing what will go

- `scratch-space` (on the login node and in jobs) lists your files due to go, each with
  its date, and shows the ones you used since the list was written as spared, looking at
  each file again as you run it:

  ```text
  Files of yours on /scratch unused for 60 days: each goes on its date unless you use it (read or write) before. 2 due (1.3T), 1 spared since the list was written.

  STATUS                         SIZE  PATH
  goes 2026-12-21                1.2T  /scratch/alice/run3/checkpoints/step_9000.pt
  goes 2026-12-21                 96G  /scratch/alice/old-dataset.tar
  spared (used 2026-12-09)        12G  /scratch/alice/eval/results.parquet
  ```

- The same list is a file in your directory, `/scratch/<login>/.purge-list` (date, size
  and path per line), rewritten every day and gone when nothing of yours is due.
- The daily disk report sends you one DM for each new removal date, whatever the disk
  report's own notices are set to (`slurm_usage_report_disk_notify`): how many files, how
  much, the date, and that `scratch-space` lists them.

### The rule

`/scratch` is for data in use. Refreshing access times only to escape the cleanup
(touching every file, a find over everything) is against the cluster's rules. The cleanup
counts old files all used within minutes of each other
(`scratch_space_purge_burst_files`) and tells the administrators in their channel;
nothing is done about it automatically.

Its metrics (`scratch_space_purge_*`, `scratch_space_last_success_timestamp_seconds`) feed
the notices and the alerts `ScratchSpaceCleanupStale`, `ScratchSpaceCleanupNeverReported`
and `ScratchSpaceCleanupTruncated` (`roles/prometheus/files/rules/scratch-space.yml`).
