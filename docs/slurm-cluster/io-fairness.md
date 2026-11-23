# I/O sharing on the NFS server

`roles/io_fairness` turns on the kernel's iocost controller for the disks behind the
NFS server's mounts, so that disk time is shared by cgroup weight: system services
first (`system.slice`, IOWeight 100), people's logins on the server after them
(`user.slice`, 50), each person equal inside it. Without it, one login unpacking an
archive in `/home` on the server can starve everything else on that disk.

## What it does not do

iocost never throttles I/O issued from the root cgroup. The NFS server's threads
(`nfsd`) run there, so what NFS clients send is never held back by it: iocost
cannot share one NFS export between the jobs of different users. What it does is keep
the server's own logins from crowding out the exports, because a saturated disk raises
latency, iocost slows its vrate, and the weighted slices wait while `nfsd` does not.
Which node and which jobs drive the NFS traffic is attributed instead (next section).

## Who does the I/O

Two sources, on every node, with the monitoring stack:

- node_exporter's `mountstats` collector counts what each node reads from and writes to
  each NFS export over the wire; Prometheus records it per node and mount point as
  `host_mount:nfs_read_bytes:rate5m` and `host_mount:nfs_write_bytes:rate5m`.
- `roles/io_usage` counts, every minute, each Slurm job's reads and writes through
  system calls (`io_usage_job_read_bytes_total`, `io_usage_job_written_bytes_total`,
  `{job_id, user}`), each login's disk I/O (`io_usage_login_*_bytes_total`,
  `{user, device}`) and how long each slice waited for I/O
  (`io_usage_slice_pressure_seconds_total`).

When a node's NFS traffic to a measured export stays above `slurm_usage_report_io_share`
(0.4) of what the disk serves (the model's `rbps` or `wbps`) for
`slurm_usage_report_io_minutes` (15), `slurm-usage-report-io.timer` (every 5 minutes)
sends the users of that node's `slurm_usage_report_io_top_jobs` (3) jobs by I/O a DM
ending in `slurm_usage_report_io_advice`, and the administrators' channel one line per
node, at once and every `slurm_usage_report_io_repeat_hours` (6) while it lasts. A root
job is named to the administrators only. Preview what it would say with
`sudo /usr/local/libexec/slurm-usage-report --io --to <your Slack member ID>`.

Grafana's **Storage / NFS and disks** dashboard shows them together: each node's NFS
reads and writes, the top jobs and logins by I/O, and for the disks under iocost whether
it is on, how busy they are, their latency and how long each slice waited for I/O. Set
the time range across the moment iocost turned on to compare before and after.

The job counters are `rchar` and `wchar` of `/proc/<pid>/io` summed over the job's
processes (the cgroup path names `job_<id>`): page cache, pipes and sockets are in them,
so they rank the jobs on a busy node, they are not NFS bytes. A task that already exited
still counts: the kernel adds a reaped child's counts to its parent, and counts a
process takes with it when it leaves the job are kept.

## Measure the disk

iocost charges each I/O what a cost model says the device pays for it. `ctrl=auto`
gives a rotational disk the kernel's generic single-HDD model, which caps a RAID at a
fraction of what it does, and the kernel's own tools (`iocost_coef_gen.py`,
`resctl-bench`) write to the raw device, which destroys the filesystem on it. So the
model is measured through the filesystem, on a scratch file, as root on the server
(`filename=` in the job names the file; change it for a mount other than `/home`):

```bash
sudo apt-get install fio
sudo fio --output-format=json --output=/root/io-model-result.json scripts/deepops/io-model.fio
sudo rm /home/.io-model-test
```

It runs six patterns for a minute each (sequential and random, reads and writes) on a
64 GiB file with O_DIRECT. A quiet hour gives the tightest latency targets; a run on a
live cluster gives looser ones, which still work. Then:

```bash
scripts/deepops/io_model.py /root/io-model-result.json /home --command "<the fio command>"
```

prints the `io_fairness_models` entry, with when it was measured and how, to paste into
`config/group_vars/`. `model` is the linear cost model; `qos` asks iocost to keep 95 %
of reads and writes within twice the random-I/O p95 the run measured, scaling the
model between 50 % and 150 % to do it.

## Turn it on

1. Set `slurm_enable_io_fairness: true` and the models, with `io_fairness_enable: false`,
   and run `playbooks/slurm-cluster/io-fairness.yml`. The model and targets are
   written with iocost off; `sudo /usr/local/sbin/io-fairness show /etc/io-fairness.json`
   shows what the kernel holds.
2. Set `io_fairness_enable: true` and run it again. `io_fairness_enabled{mount}` on the
   host says it is on; `/sys/fs/cgroup/<slice>/io.stat` shows each slice's I/O and
   `io.pressure` how long its tasks waited for the disk.

`slurm_enable_io_fairness: false` turns iocost off, gives each disk back the kernel's
model and removes the slice weights. The model is written again at every boot
(`io-fairness.service`), resolving each mount to its whole disk then.
