# Boot failures that take a cluster down

Two failure modes below took every node of a cluster off the network at once. Neither
announced itself as what it was, and both are general: nothing about them depends on the
site that hit them. This is what they look like and how to tell them apart.

## `ProtectHome=` cannot namespace over an autofs `/home`

### What it looks like

The console fills with core services failing and being restarted, and the boot never
finishes:

```
[FAILED] Failed to start polkit.service - Authorization Manager.
[DEPEND] Dependency failed for ModemManager.service - Modem Manager.
[FAILED] Failed to start systemd-logind.service - User Login Management.
[FAILED] Failed to start systemd-networkd.service - Network Configuration.
         ... the same four, over and over ...
[*] (4 of 4) Job e2scrub_reap.service/start running (1h 59min 9s / no limit)
```

The journal names it exactly:

```
systemd-networkd.service: Failed to set up mount namespacing: /home: No such device
systemd-networkd.service: Main process exited, code=exited, status=226/NAMESPACE
```

### Why

A unit with `ProtectHome=` gets a mount namespace in which `/home` is hidden or read-only,
and building that namespace means operating on `/home`. Where `/home` is an autofs mount
point that nothing has triggered, the kernel answers `ENODEV` and the service exits
`226/NAMESPACE`.

`ProtectHome=` is not exotic. On a current systemd it is set by `systemd-networkd`,
`systemd-logind`, `polkit`, `systemd-timedated` and `chrony` among others:

```bash
for u in systemd-networkd systemd-logind polkit systemd-timedated chrony; do
  printf '%-22s %s\n' "$u" "$(systemctl cat $u.service | grep -E '^ProtectHome=')"
done
```

They fail together, systemd restarts them, and they fail again.

### Why the boot never ends

`e2scrub_reap.service` also sets `ProtectHome=`, is `Type=oneshot` -- which has no start
timeout, hence `no limit` on the console -- and is `WantedBy=multi-user.target`. The
target waits on it forever, so the boot never reaches a login prompt.

It is worth knowing that the unit had nothing to do: `e2scrub` reaps snapshots of LVM
logical volumes, and these nodes had no LVM. It never reached `ExecStart`. The journal
shows `Starting e2scrub_reap.service...` and then nothing, which is the signature of a
service that failed while its namespace was being built rather than while it ran.

### What to do

Do not put `x-systemd.automount` on `/home`. It is a reasonable answer to a client that
boots before its NFS server -- the trigger is established without contacting the server
and the mount happens on first access -- but not for a path the service sandboxing walks
into. Set it per mount for a share nothing sandboxes; see [Slurm and NFS](../slurm-cluster/slurm-nfs.md).

To get a stuck node far enough to fix it, mask the unit that is blocking the target:

```
systemd.mask=e2scrub_reap.service
```

on the kernel command line. That clears the symptom; the mount option is the cause.

### Why it is intermittent

If something triggers `/home` early and the mount succeeds, every later `ProtectHome=`
service is fine. Whether that happens is a race, so nodes fail differently on different
boots and one node in a fleet can come back while the rest do not.

## A socket group that only exists in the directory

### What it looks like

Docker is not running, and has not been since the last boot:

```
docker.socket: Failed to resolve group <group>: No such process
```

`systemctl --failed` shows `docker.socket` and `docker.service`. Nothing retries, so the
node runs without Docker until somebody notices.

### Why

`dockerd` resolves the socket's group by name when it creates the socket. Where that
group is published through a directory -- NIS, LDAP -- the resolution happens before the
directory client has bound, and fails.

### What to do

Publish the group in the local files as well, with the GID pinned to the directory's, so
`files` answers first and the boot does not depend on the directory being up. That is what
`docker_socket_group_create_local` does.

There is a trap in doing it by hand: `ansible.builtin.group` decides whether a group
exists by resolving it through NSS, sees the directory's entry, and creates nothing. Check
the file, not the resolver:

```bash
grep '^<group>:' /etc/group     # empty means the local entry is missing
getent group <group>            # answers either way
```

## Telling them apart

Both present as a node that will not finish booting, and both take the network with them,
so neither can be diagnosed over SSH. One console read separates them:

| Console shows | Cause |
| --- | --- |
| `226/NAMESPACE`, a `no limit` job, core services flapping | autofs under `ProtectHome=` |
| `Failed to resolve group`, only Docker units failed | directory group missing locally |

Both need a console on a node whose network is gone. Arrange that before it is needed:
[out-of-band management](bmc.md).
