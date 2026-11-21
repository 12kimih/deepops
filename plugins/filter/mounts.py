"""The mount point a path lives on, from a host's mounts.

"/var/spool/slurmd-private" | mount_point(ansible_facts.mounts)  -> "/" (or "/var" ...)

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


class FilterModule:
    def filters(self):
        return {"mount_point": mount_point}
