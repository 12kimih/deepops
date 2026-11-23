"""cleanup: the folders worth cleaning on a filesystem, one way for every scanner.

    walk(host, top, dev, skip, budget)       -> (path, lstat) of every file under TOP
    folders = Folders(bases)                 add(path, owner, size, used) per file
    folders.ranked(now, stale_days, top)     -> the TOP folders, most worth cleaning first
    values(filesystem, ranked, now)          -> their textfile metrics (cluster_tools.prom)

A folder is a directory one level below a base (a person's directory, a collaboration
folder): /home/alice/old-runs, /scratch/projects/vision/cache. Its size is its files'
total, its last use the newest of its files' (the later of access and modification
time), its owner whoever holds most of its bytes. It is worth cleaning when it has gone
unused past the stale period: ranked by size times the days past it, so a large folder
long unused comes first. The figures are exported as metrics, one series per folder:

  disk_cleanup_candidate_bytes{filesystem, path, user}
  disk_cleanup_candidate_unused_days{filesystem, path, user}
  disk_cleanup_scan_timestamp_seconds{filesystem}

and the daily disk report (cluster_tools.disk_report) lists them; nothing is removed
here. The scanners: cluster_tools.disk_quota (scan, weekly, the home server) and
cluster_tools.scratch_space (its daily purge walk, no second one).
Python 3.9 or newer, standard library only.
"""

import os
import stat
from collections import defaultdict

from .prom import key

DAY = 86400


def walk(host, top, dev, skip, budget):
    """(path, lstat) of every regular file and symbolic link under TOP (str) on device
    DEV, never following a link nor entering another filesystem, SKIP (paths) left out;
    BUDGET ([n]) counts the entries looked at down to 0, which stops the walk. HOST has
    scandir and lstat (os's, or a test's)."""
    stack = [top]
    while stack:
        path = stack.pop()
        try:
            entries = list(host.scandir(path))
        except OSError:
            continue
        for entry in entries:
            if budget[0] <= 0:
                return
            budget[0] -= 1
            child = os.path.join(path, entry.name)
            if child in skip:
                continue
            try:
                st = host.lstat(child)
            except OSError:
                continue
            if st.st_dev != dev:
                continue
            if stat.S_ISDIR(st.st_mode):
                stack.append(child)
            elif stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
                yield child, st


def last_use(st):
    """When a file was last used: read (its access time) or written."""
    return max(st.st_atime, st.st_mtime)


class Folders:
    """Each folder's size, last use and bytes per owner, from its files."""

    def __init__(self, bases):
        # Deepest first, so a base inside another (a projects directory) wins.
        self.bases = sorted((b.rstrip("/") for b in bases), key=lambda b: -len(b))
        self.size = defaultdict(int)
        self.used = defaultdict(float)
        self.owners = defaultdict(lambda: defaultdict(int))

    def folder(self, path):
        """PATH's folder: its base and the first component below it (the base itself for
        a file directly in it), or None outside every base."""
        for base in self.bases:
            if path.startswith(base + "/"):
                rest = path[len(base) + 1 :]
                return os.path.join(base, rest.split("/", 1)[0]) if "/" in rest else base
        return None

    def add(self, path, owner, size, used):
        name = self.folder(path)
        if name is None:
            return
        self.size[name] += size
        self.used[name] = max(self.used[name], used)
        self.owners[name][owner] += size

    def ranked(self, now, stale_days, top):
        """[{path, user, bytes, unused_days}] of the TOP folders unused past STALE_DAYS,
        by bytes times the days past it, most first."""
        out = []
        for name, nbytes in self.size.items():
            unused = int((now - self.used[name]) // DAY)
            if unused <= stale_days or nbytes <= 0:
                continue
            user = max(self.owners[name].items(), key=lambda kv: (kv[1], kv[0]))[0]
            out.append({"path": name, "user": user, "bytes": nbytes, "unused_days": unused})
        out.sort(key=lambda c: (-(c["bytes"] * (c["unused_days"] - stale_days)), c["path"]))
        return out[:top]


def values(filesystem, ranked, now):
    """The metrics of FILESYSTEM's RANKED folders, as {key: value} (cluster_tools.prom)."""
    out = {}
    for c in ranked:
        labels = {"filesystem": filesystem, "path": c["path"], "user": c["user"]}
        out[key("disk_cleanup_candidate_bytes", **labels)] = c["bytes"]
        out[key("disk_cleanup_candidate_unused_days", **labels)] = c["unused_days"]
    out[key("disk_cleanup_scan_timestamp_seconds", filesystem=filesystem)] = int(now)
    return out
