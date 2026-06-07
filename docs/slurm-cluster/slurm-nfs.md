# Slurm NFS

Slurm cluster configuration for NFS filesystems

- [Slurm NFS](#slurm-nfs)
  - [Introduction](#introduction)
  - [Configuring NFS shares from the Slurm control node](#configuring-nfs-shares-from-the-slurm-control-node)
    - [Exports from the Slurm control node](#exports-from-the-slurm-control-node)
    - [NFS mounts on the clients](#nfs-mounts-on-the-clients)
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

As above, you can add as many additional mounts to the list as you wish.

The `options` field for each mount specifies the NFS options used to mount the filesystem.
For the available NFS options, see the manpages for your Linux distribution: `man 5 nfs`.

## Performance tuning for random I/O

A single NFS head serving `/home` to many GPU nodes commonly becomes a **random-I/O
bottleneck** under job load: one server is a single network/metadata chokepoint, a
wide RAID5 group pays a 4x read-modify-write penalty on every small random write, and
hot job I/O runs directly against the share. No single knob fixes this -- separating hot
I/O from NFS is the durable fix -- but the DeepOps defaults now apply the high-ROI
config quick-wins. In priority order:

1. **Keep hot I/O on node-local NVMe, not NFS.** Container import/unpack and dataset
   small-file reads are latency-bound random I/O that melts a single NFS head. The
   `enroot_*_path` defaults are already node-local -- **do not** repoint them at an NFS
   mount (see the warning in `config.example/group_vars/all.yml`). Stage datasets to
   local `/tmp` (or a mounted NVMe scratch via `TMPDIR`/`sbcast`), compute locally,
   copy results back.

2. **Client mount options.** The default `nfs_mounts` options are now
   `rw,hard,vers=4.2,nconnect=8,rsize=1048576,wsize=1048576,proto=tcp,timeo=600,retrans=2,noatime,_netdev,nofail`.
   `vers=4.2` uses COMPOUND ops; `nconnect=8` opens 8 TCP connections per mount (max 16)
   to beat the single-flow limit -- size it to the link (4 @40G / 8 @100G / 16 @200G),
   needs kernel >= 5.3, and keep it identical on every mount to the same server. Note the
   old `async` here was a **no-op** (it is a server `/etc/exports` option, not a client
   mount option). Append `,fsc` and run the `cachefilesd` role to cache repeat reads.
   (`man 5 nfs`)

3. **Server `nfsd` thread count.** The Linux default is only **8** threads -- far too few
   for many clients under random I/O (each thread serves one RPC and blocks on disk).
   Set `nfs_server_threads` (default `32`; busy servers run 64-256). Verify with
   `cat /proc/fs/nfsd/threads` and size it by watching `sockets-enqueued` grow in
   `/proc/fs/nfsd/pool_stats` under load. Do **not** use the `/proc/net/rpc/nfsd` `th`
   histogram -- it was removed in kernel 2.6.32 and always reads zero. (`rpc.nfsd(8)`)

4. **Server socket buffers / exports.** Raise `net.core.{r,w}mem_max` and
   `netdev_max_backlog` and restart NFS; consider MTU 9000 end-to-end. `no_subtree_check`
   is already the default. Use export `async` only for reproducible scratch (biggest
   throughput lever, but a crash loses unflushed data).

5. **Hardware (operator action, not a DeepOps setting): RAID5 -> RAID10.** A 17-disk
   RAID5 pays 4 physical I/Os per random write and has a slow, risky rebuild. RAID10 (or
   ZFS mirror vdevs + NVMe SLOG + a metadata special vdev) gives far better random-write
   IOPS. For 8 nodes, tuned NFS + local NVMe staging is usually enough; only graduate to
   a parallel filesystem (BeeGFS/Lustre/WekaFS) if GPUs are provably starved.

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
