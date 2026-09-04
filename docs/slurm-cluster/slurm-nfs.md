# Slurm NFS

Slurm cluster configuration for NFS filesystems

- [Slurm NFS](#slurm-nfs)
  - [Introduction](#introduction)
  - [Configuring NFS shares from the Slurm control node](#configuring-nfs-shares-from-the-slurm-control-node)
    - [Exports from the Slurm control node](#exports-from-the-slurm-control-node)
    - [NFS mounts on the clients](#nfs-mounts-on-the-clients)
  - [Mounting reliably at boot](#mounting-reliably-at-boot)
  - [Performance tuning for random I/O](#performance-tuning-for-random-io)
  - [Configuring a separate NFS server](#configuring-a-separate-nfs-server)
  - [Disabling NFS](#disabling-nfs)

## Introduction

Slurm clusters typically depend on the presence of one or more shared filesystems, mounted on all the nodes in the cluster.
Having a shared filesystem simplifies software installation and provides a common working space for user jobs,
and many common HPC applications depend on the presence of such a filesystem.

Our default configuration in DeepOps achieves this by configuring the Slurm control/login node as an NFS server,
and the compute nodes as clients of the NFS server.

## Configuring NFS shares from the Slurm control node

By default, we configure two NFS exports from the control node to the compute nodes:

- `/home`: The user home directory space is shared across all nodes in the cluster.
  This is a common pattern on most HPC clusters.
- `/sw`: This directory provides a separate directory for installing software that needs to be built from source.
  In most clusters this will be an admin-only area, not writeable by regular users, but this is a choice for the cluster admin.

If you would like to make changes to that configuration, you can do so be setting the following variables.

### Exports from the Slurm control node

```yaml
nfs_exports:
  - path: "<absolute path of exported directory on control node>"
    options: "<ips allowed to mount>(<options for the NFS export>)"
  - path: "<absolute path of another exported directory>"
    options: "<ips allowed to mount>(<options for the NFS export>)"
```

You can add as many additional exports to the list as you wish, configuring each appropriately.

The `options` field for each export, which specifies the IPs allowed to mount these exports and the options for the export, follows the format of the NFS `/etc/exports` file.
For documentation on the available NFS export options, see the manpages for your Linux distribution: `man 5 exports`.

### NFS mounts on the clients

```yaml
nfs_mount_options: "<nfs mount options>"
nfs_mounts:
  - mountpoint: "<absolute path of directory to mount share on clients>"
    server: "<hostname of NFS server>"
    path: "<path of the export from the server>"
  - mountpoint: "<absolute path of another directory to mount share on clients>"
    server: "<hostname of NFS server>"
    path: "<path of the export from the server>"
    options: "<nfs mount options for this mount only>"
```

As above, you can add as many additional mounts to the list as you wish.

`nfs_mount_options` is the single place to tune the client mount options: every entry
that does not set its own `options` is mounted with it, so a cluster tunes one string
instead of repeating it per mount. An entry that does set `options` overrides it
completely -- use that only for a mount that genuinely differs (a filer that needs a
smaller `rsize`, a share on a different fabric). For the available NFS options, see the
manpages for your Linux distribution: `man 5 nfs`.

## Mounting reliably at boot

A plain `/etc/fstab` entry is attempted exactly once, at boot. When a whole cluster
powers on together, a client regularly reaches `remote-fs.target` before its NFS server
is serving; the mount fails, `nofail` lets the boot continue, and nothing ever retries --
so the node comes up with `/home` missing and only `root` (whose home is local) can log
in. Boot ordering cannot fix this in general either: a head node serving `/home` and a
storage node serving `/scratch` mount each other, so neither of them can go first.

`x-systemd.automount` looks like the answer and is not, at least not for `/home`.
It makes `systemd-fstab-generator` create an `.automount` unit: the trigger is
established without contacting the server and the share is mounted on first access, so
boot order stops mattering. But a service with `ProtectHome=` cannot set up its mount
namespace over an autofs `/home`. It fails with

```
Failed to set up mount namespacing: /home: No such device
... Main process exited, code=exited, status=226/NAMESPACE
```

and `systemd-networkd`, `systemd-logind`, `polkit`, `systemd-timedated` and `chrony` all
set `ProtectHome=`. They fail together, systemd restarts them, and they fail again. The
node never finishes booting, because `e2scrub_reap.service` -- `ProtectHome=read-only`,
`Type=oneshot`, so no start timeout -- is pulled in by `multi-user.target` and blocks
there for good. A whole cluster went down this way.

So the default carries no `x-systemd.automount`. Set it per mount for a share nothing
sandboxes, never for `/home`.

The boot race above is real and still unsolved here; `nofail` means a node that loses the
race comes up without the share rather than not at all. If you need a retry, add one that
does not put autofs on `/home` -- a `noauto` entry plus a unit that retries `mount`, or
the `autofs` daemon with a wildcard map under `/home/<user>` rather than on `/home`
itself (see `roles/autofs` and `autofs_map` in `group_vars/all.yml`).

Consequences worth knowing:

- **A shutdown must not depend on the servers.** A node holding a `hard` mount from a
  server rebooting alongside it blocks in uninterruptible sleep during its own shutdown
  and never reaches the reboot -- left powered on, off the network, and out of reach.
  `nfs_detach_on_shutdown` (default true) installs a unit that lazily detaches the mounts
  on the way down; a lazy detach sends no packets, so a server that has already gone
  cannot stall it. `playbooks/utilities/reboot.yml` does the same before it reboots,
  covering nodes that do not have the unit yet.
- **NHC walks into each mountpoint before reading `/proc/mounts`.** `nhc_check_mounts`
  emits a `check_file_test` ahead of `check_fs_mount_rw`. Harmless for an eager mount,
  and it keeps the check correct if a site does enable an automount elsewhere. See
  [Node Health Check](README.md#node-health-check).

## Performance tuning for random I/O

A single NFS server can become a **random-I/O bottleneck** under job load: it is one
network/metadata chokepoint, parity RAID (RAID5/6) pays a read-modify-write penalty on
small random writes, and hot job I/O run directly against the share competes with every
other node. No single knob fixes this -- separating hot I/O from NFS is the durable fix
-- but DeepOps ships sensible defaults and exposes the knobs below; **tune them for your
hardware in `config/group_vars/slurm-cluster.yml`** (site-specific values belong in
`config/`, never in the role defaults). In priority order:

1. **Keep hot I/O on node-local NVMe, not NFS.** Container import/unpack and dataset
   small-file reads are latency-bound random I/O that melts a single NFS head. The
   `enroot_*_path` defaults are already node-local -- **do not** repoint them at an NFS
   mount (see the warning in `config.example/group_vars/all.yml`). Stage datasets to
   local `/tmp` (or a mounted NVMe scratch via `TMPDIR`/`sbcast`), compute locally,
   copy results back.

2. **Client mount options.** The default `nfs_mount_options` is now
   `rw,hard,vers=4.2,nconnect=16,rsize=1048576,wsize=1048576,proto=tcp,timeo=600,retrans=2,noatime,_netdev,nofail,x-systemd.automount`.
   `vers=4.2` uses COMPOUND ops; `nconnect=16` opens 16 TCP connections per mount to beat
   the single-flow limit. **The default targets a 100GbE+ fabric** -- NetApp shows
   `nconnect` reaching ~line rate (~11 GB/s) on a single 100G NIC at 16 connections. On
   10/25GbE, lower it to ~4 (gains plateau past 4-8, and too many connections can saturate
   a slower link); max 16. Needs kernel >= 5.3, and keep it identical on every mount to the
   same server. Note `nconnect`
   helps *bandwidth* (streaming, checkpoints), not small-file/metadata random I/O. The
   old `async` here was a **no-op** (it is a server `/etc/exports` option, not a client
   mount option). Append `,fsc` and run the `cachefilesd` role to cache repeat reads.
   (`man 5 nfs`)

3. **Server `nfsd` thread count.** The Linux default is only **8** threads -- far too few
   for many clients under random I/O (each thread serves one RPC and blocks on disk).
   `nfs_server_threads` defaults to **128** -- set it explicitly in `config/`
   (busy servers run 64-256; lower it on small servers, raise it if thread-starved). We do not
   auto-size to CPU count: very high counts (e.g. 192) can fail to start with ENOMEM
   on a busy, long-running server. Verify with
   `cat /proc/fs/nfsd/threads` and size it by watching `sockets-enqueued` grow in
   `/proc/fs/nfsd/pool_stats` under load. Do **not** use the `/proc/net/rpc/nfsd` `th`
   histogram -- it was removed in kernel 2.6.32 and always reads zero. (`rpc.nfsd(8)`)

4. **Server/client socket buffers.** DeepOps now raises the TCP buffers for the NFS
   data path automatically (`nfs_tune_network: true` -> `/etc/sysctl.d/30-nfs-tuning.conf`:
   `net.core.{r,w}mem_max=2147483647` (~2 GiB, the ESnet 100G ceiling), 1 GiB
   `tcp_{r,w}mem` autotune ceilings, `netdev_max_backlog=300000`). Set it false to
   opt out.

5. **Jumbo frames (MTU 9000) -- often a top win on high-bandwidth networks.** This is a
   network/NIC/switch setting (not a DeepOps var, since it lives in your netplan and
   switch config), but on a fast link it is frequently the single highest-ROI change: it
   cuts per-packet overhead and lets NFS approach line rate. Enable MTU 9000 on the
   storage NIC, every client NIC, AND the switch -- a mismatch causes fragmentation and
   makes things *worse*.

6. **Per-export options (set in `nfs_exports` `options`).** Keep `/home` (user data)
   `sync` for integrity. For a reproducible `/scratch` or `/data` export, use
   `async,no_wdelay` -- `async` is the single biggest throughput lever (a crash loses
   unflushed data, acceptable for scratch) and `no_wdelay` suits the small random writes
   of an NVMe backend. `no_subtree_check` is already the default. Example:
   `nfs_exports: [{path: /scratch, options: "*(rw,async,no_wdelay,no_root_squash)"}]`.

7. **Storage and network topology (your hardware, configured outside DeepOps).**
   General principles to apply to whatever you run:
   - Parity RAID (RAID5/6) pays a read-modify-write penalty on small random writes;
     RAID10 (or ZFS mirror vdevs + a metadata special vdev) is far better for random I/O
     on a spinning-disk home.
   - Match expectations to your **network**: if the storage is faster than the link
     (e.g. an NVMe array on a 10/25GbE network), NFS is *network-bound* -- the array's
     value over NFS is then low latency + high IOPS, not raw bandwidth, so even a parity
     RAID is fine because the link caps throughput well below the array.
   - For bandwidth-bound jobs, stage to **node-local** NVMe (keep enroot/`TMPDIR` local).
     To exceed a single link, bond multiple NICs (LACP + `nconnect`) or move to a faster
     fabric; graduate to a parallel filesystem (BeeGFS/Lustre/WekaFS) only if GPUs are
     provably starved.

## NFS server auto-recovery

`nfs-server.service` is a `Type=oneshot` unit, so systemd ignores `Restart=` on it: a
failed *start* -- for example `rpc.nfsd` exiting with `ENOMEM` when a package upgrade
restarts the NFS stack -- otherwise leaves the server down until an operator intervenes.

DeepOps installs a small safety net (enabled by default) that turns a failed start into
a brief, self-healing event: an `OnFailure=` drop-in on `nfs-server.service` starts a
helper unit (`nfs-server-restart.service`) that waits, then retries
`systemctl restart nfs-server`. It is bounded, so a *persistent* failure still stops
(rather than flapping forever) for an operator to notice. Tune or disable it in
`config/group_vars/slurm-cluster.yml`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `nfs_server_autorestart` | `true` | Enable the `OnFailure` auto-recovery. |
| `nfs_server_autorestart_retries` | `10` | Max retries before giving up (`StartLimitBurst`). |
| `nfs_server_autorestart_delay` | `30` | Seconds between retries (`ExecStartPre` sleep). |
| `nfs_server_autorestart_interval` | `600` | Rate-limit window in seconds; must exceed `retries * delay`. |

## Configuring a separate NFS server

If your site already has an NFS server, you may wish to use your existing server rather than setting up the Slurm control node to serve NFS.
To configure DeepOps to use your existing server, you should set the following configuration values:

- Set `slurm_enable_nfs_server` to `false`

- Set `nfs_client_group` to `"slurm-cluster"`

- Configure the `nfs_mounts` variable as shown below, repeating the list item for each NFS export

```yaml
nfs_mounts:
  - mountpoint: "<absolute path of directory to mount share on clients>"
    server: "<hostname of NFS server>"
    path: "<path of the export from the server>"
    options: "<nfs mount options>"
  - mountpoint: "<absolute path of another directory to mount share on clients>"
    server: "<hostname of NFS server>"
    path: "<path of the export from the server>"
    options: "<nfs mount options>"
```

## Disabling NFS

If you want to disable the use of any NFS mounts, or want to configure NFS yourself outside of DeepOps, set the following variables:

- Set `slurm_enable_nfs_server` to `false`
- Set `slurm_enable_nfs_client_nodes` to `false`
