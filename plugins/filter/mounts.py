"""Mounts: the mount point a path lives on, and the entries of an fstab.

"/var/spool/slurmd-private" | mount_point(ansible_facts.mounts)  -> "/" (or "/var" ...)
fstab_text | fstab_entries   -> [{src, path, fstype, opts, dump, passno}, ...]
projects | project_filesystems(ansible_facts.mounts) -> each with its "filesystem"
declared | mount_options_pending(ansible_facts.mounts) -> mount points still without
    an option the kernel takes only when it mounts (an XFS quota)

The deepest mount at or above the path, by whole path components: /var/tmpfoo is
not under /var/tmp. The path need not exist yet. Tools that check a filesystem by
its mount point (NHC's check_fs_free, df's mount column) need this one: given any
other directory, NHC's check matches nothing and passes.
"""


def _parts(path):
    return [p for p in str(path).split("/") if p]


def mount_point(path, mounts):
    if not str(path).startswith("/"):
        raise ValueError(f"{path!r} is not an absolute path")
    want = _parts(path)
    best = None
    for entry in mounts or []:
        mount = entry.get("mount") if isinstance(entry, dict) else entry
        parts = _parts(mount)
        if want[: len(parts)] == parts and (best is None or len(parts) > len(_parts(best))):
            best = mount
    if best is None:
        raise ValueError(f"no mount holds {path!r} (is / among the mounts?)")
    return best


def project_filesystems(projects, mounts):
    """PROJECTS ({path: settings}) with each one's "filesystem": the mount point its
    path lives on, which project quotas are set on (cluster_tools.disk_quota)."""
    return {
        path: {**(settings or {}), "filesystem": mount_point(path, mounts)}
        for path, settings in (projects or {}).items()
    }


# Filesystems another host serves: their options are the mounting role's (nfs_client).
NETWORK_FSTYPES = ("nfs", "nfs4", "cifs", "smb3", "fuse.sshfs", "glusterfs", "ceph", "lustre")


def fstab_entries(text):
    """The entries of fstab(5) TEXT in order, comments and blank lines skipped; dump and
    passno default to 0 as mount(8) reads them. Octal escapes (\\040 for a space) are
    kept as written, so an entry is written back exactly."""
    entries = []
    for line in str(text).splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) < 3:
            continue
        fields += ["defaults", "0", "0"][len(fields) - 3 :]
        src, path, fstype, opts, dump, passno = fields[:6]
        entries.append(
            {
                "src": src,
                "path": path,
                "fstype": fstype,
                "opts": opts,
                "dump": dump,
                "passno": passno,
                "network": fstype in NETWORK_FSTYPES,
            }  # fmt: skip
        )
    return entries


# Options XFS reads only when it mounts: a remount with them returns success and leaves
# them off, so a remount alone never shows they are missing. Each as the kernel lists it.
MOUNT_TIME_ONLY = {
    "xfs": {
        "usrquota": "usrquota", "uquota": "usrquota", "quota": "usrquota", "uqnoenforce": "usrquota",
        "grpquota": "grpquota", "gquota": "grpquota", "gqnoenforce": "grpquota",
        "prjquota": "prjquota", "pquota": "prjquota", "pqnoenforce": "prjquota",
    },
}  # fmt: skip


def mount_options_pending(declared, mounts):
    """The mount points of DECLARED ({mount point: options}) whose mounted filesystem
    lacks an option of MOUNT_TIME_ONLY that the declaration names: they take it only at
    the next mount (a reboot)."""
    live = {m["mount"]: m for m in mounts or [] if isinstance(m, dict) and "mount" in m}
    pending = []
    for path, options in sorted((declared or {}).items()):
        entry = live.get(path)
        if entry is None:
            continue
        only = MOUNT_TIME_ONLY.get(entry.get("fstype"), {})
        want = {only[o] for o in str(options).split(",") if o in only}
        have = set(str(entry.get("options", "")).split(","))
        if want - have:
            pending.append(path)
    return pending


class FilterModule:
    def filters(self):
        return {
            "mount_point": mount_point,
            "fstab_entries": fstab_entries,
            "project_filesystems": project_filesystems,
            "mount_options_pending": mount_options_pending,
        }
