"""scratch_alloc: space on the fast scratch filesystem, handed out per request, each with
its own size limit (an XFS project quota) and an end date.

    scratch-alloc CONFIG.json create USER SIZE DAYS [REASON]   a new allocation
    scratch-alloc CONFIG.json resize ID SIZE                   a new size limit
    scratch-alloc CONFIG.json extend ID DAYS                   DAYS more from today or its end
    scratch-alloc CONFIG.json release ID                       given back now
    scratch-alloc CONFIG.json list [USER]                      every allocation, as JSON
    scratch-alloc CONFIG.json sweep                            the end dates kept (a timer)
    scratch-alloc CONFIG.json metrics                          each allocation's size and use

Run as root on the host whose XFS filesystem holds the scratch space. An allocation is
the directory <root>/<user>-<id>, the user's alone (0700), whose project quota is its
size; the registry (a JSON file next to it, written atomically under a lock) is the
record of every allocation, so a request is applied the moment it is accepted.

At its end date an allocation is frozen: its limit drops to what it holds, so nothing
more can be written but everything can be read and copied away. After grace_days
(the cluster's period for users to clean up) it is removed. A released one is moved
aside at once and removed by the next sweep, so release never waits on a large delete.
Who may ask for what (sizes and periods allowed without an administrator) is the
cluster bot's to decide; this tool refuses only what the configuration's hard caps
forbid.

CONFIG: {"mount": "/scratch", "root": "/scratch/alloc", "registry": ".../registry.json",
         "first_id": 1000, "max_bytes": "20T", "max_days": 180, "grace_days": 14,
         "textfile": "/run/prometheus/scratch-alloc.prom"}
Python 3.9 or newer, standard library only.
"""

import fcntl
import json
import os
import pwd
import shutil
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from . import proc
from .cli import run_main
from .disk_quota import Error as QuotaError
from .disk_quota import kib
from .files import write_atomic
from .prom import key, render

MOUNTS = "/proc/self/mounts"
# XFS mount options that turn project quota accounting and enforcement on.
PROJECT_QUOTA = {"prjquota", "pquota", "pqnoenforce"}
TRASH = ".released"
DAY = 86400
TIMEOUT = 120


class Error(Exception):
    """A request or a host the tool refuses, said plainly."""


class Host:
    """The commands and files the tool reads and changes: replaced in the tests."""

    def read(self, path):
        return Path(path).read_text()

    def xfs_quota(self, mount, command):
        return proc.run(
            ["xfs_quota", "-x", "-c", command, mount], timeout=TIMEOUT, capture_output=True, text=True
        ).stdout

    def make_dir(self, path, uid, gid):
        os.mkdir(path, 0o700)
        os.chown(path, uid, gid)
        os.chmod(path, 0o700)

    def rename(self, src, dst):
        os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
        os.rename(src, dst)

    def remove_tree(self, path):
        shutil.rmtree(path, ignore_errors=True)

    def exists(self, path):
        return os.path.lexists(path)

    def user(self, name):
        try:
            entry = pwd.getpwnam(name)
        except KeyError:
            return None
        return entry.pw_uid, entry.pw_gid


def nbytes(size):
    try:
        return kib(size) * 1024
    except QuotaError as e:
        raise Error(str(e)) from None


def check_mount(host, mount):
    """The scratch filesystem is XFS mounted with project quotas, or nothing can be limited."""
    for line in host.read(MOUNTS).splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[1] == mount:
            if fields[2] != "xfs":
                raise Error(f"{mount} is {fields[2]}, not XFS: per-allocation limits need XFS project quotas")
            if not PROJECT_QUOTA & set(fields[3].split(",")):
                raise Error(f"{mount} is mounted without project quotas: add prjquota to its mount options")
            return
    raise Error(f"{mount} is not mounted")


class Registry:
    """The allocations, read and written whole under an exclusive lock."""

    def __init__(self, path, first_id):
        self.path, self.first_id = path, first_id

    @contextmanager
    def open(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        with open(self.path + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = json.loads(Path(self.path).read_text())
            except FileNotFoundError:
                data = {"next_id": self.first_id, "allocations": {}}
            yield data
            write_atomic(self.path, json.dumps(data, indent=1, sort_keys=True) + "\n", mode=0o600)


def used_bytes(host, mount, pid):
    """What project PID holds now, in bytes (xfs_quota counts 1 KiB blocks)."""
    out = host.xfs_quota(mount, f"quota -p -N -b {pid}")
    for line in out.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1].isdigit():
            return int(fields[1]) * 1024
    return 0


def set_limit(host, mount, pid, limit):
    kb = max(limit // 1024, 0)
    host.xfs_quota(mount, f"limit -p bsoft={kb}k bhard={kb}k {pid}")


def find(data, alloc_id):
    entry = data["allocations"].get(str(alloc_id))
    if not entry:
        raise Error(f"no allocation {alloc_id}")
    return entry


def create(host, cfg, data, user, size, days, reason, now):
    ids = host.user(user)
    if not ids:
        raise Error(f"no account {user!r}")
    limit, days = nbytes(size), int(days)
    if limit <= 0 or limit > nbytes(cfg["max_bytes"]):
        raise Error(f"{size} is outside 1 KiB to {cfg['max_bytes']}")
    if not 0 < days <= cfg["max_days"]:
        raise Error(f"{days} days is outside 1 to {cfg['max_days']}")
    pid = data["next_id"]
    path = os.path.join(cfg["root"], f"{user}-{pid}")
    if host.exists(path):
        raise Error(f"{path} exists already")
    host.make_dir(path, *ids)
    host.xfs_quota(cfg["mount"], f"project -s -p {path} {pid}")
    set_limit(host, cfg["mount"], pid, limit)
    data["next_id"] = pid + 1
    data["allocations"][str(pid)] = {
        "user": user, "path": path, "bytes": limit, "created": int(now),
        "expires": int(now + days * DAY), "state": "active", "reason": reason, "extensions": 0,
    }  # fmt: skip
    return data["allocations"][str(pid)] | {"id": pid}


def resize(host, cfg, data, alloc_id, size):
    entry = find(data, alloc_id)
    if entry["state"] != "active":
        raise Error(f"allocation {alloc_id} is {entry['state']}: extend it first")
    limit = nbytes(size)
    if limit <= 0 or limit > nbytes(cfg["max_bytes"]):
        raise Error(f"{size} is outside 1 KiB to {cfg['max_bytes']}")
    set_limit(host, cfg["mount"], alloc_id, limit)
    entry["bytes"] = limit
    return entry | {"id": int(alloc_id)}


def extend(host, cfg, data, alloc_id, days, now):
    """DAYS more, counted from the end date (or from today once it has passed); a frozen
    allocation gets its size limit back."""
    entry = find(data, alloc_id)
    if entry["state"] == "released":
        raise Error(f"allocation {alloc_id} was released")
    days = int(days)
    start = max(entry["expires"], now)
    if not 0 < days or start + days * DAY > now + cfg["max_days"] * DAY:
        raise Error(f"an allocation ends at most {cfg['max_days']} days from today")
    if entry["state"] == "frozen":
        set_limit(host, cfg["mount"], alloc_id, entry["bytes"])
        entry["state"] = "active"
    entry["expires"] = int(start + days * DAY)
    entry["extensions"] = entry.get("extensions", 0) + 1
    return entry | {"id": int(alloc_id)}


def release(host, cfg, data, alloc_id, now):
    """Given back now: out of the user's sight at once, removed by the next sweep."""
    entry = find(data, alloc_id)
    if entry["state"] == "released":
        return entry | {"id": int(alloc_id)}
    if host.exists(entry["path"]):
        host.rename(entry["path"], os.path.join(cfg["root"], TRASH, str(alloc_id)))
    entry["state"], entry["released"] = "released", int(now)
    return entry | {"id": int(alloc_id)}


def sweep(host, cfg, data, now):
    """Freeze what reached its end date, remove what stayed frozen past the grace period
    and what was released; each change as a line."""
    out, grace = [], cfg["grace_days"] * DAY
    for alloc_id, entry in sorted(data["allocations"].items(), key=lambda kv: int(kv[0])):
        if entry["state"] == "active" and now >= entry["expires"]:
            set_limit(host, cfg["mount"], alloc_id, max(used_bytes(host, cfg["mount"], alloc_id), 4096))
            entry["state"], entry["frozen"] = "frozen", int(now)
            out.append(f"froze {alloc_id} ({entry['user']}): ended, read-only for {cfg['grace_days']} days")
        elif entry["state"] == "frozen" and now >= entry["expires"] + grace:
            out += [f"released {alloc_id} ({entry['user']}): grace period over"]
            release(host, cfg, data, alloc_id, now)
        if entry["state"] == "released":
            trash = os.path.join(cfg["root"], TRASH, alloc_id)
            if host.exists(trash):
                host.remove_tree(trash)
            # Its files are gone with it, so the project ID only needs its limit cleared.
            set_limit(host, cfg["mount"], alloc_id, 0)
            del data["allocations"][alloc_id]
            out.append(f"removed {alloc_id} ({entry['user']})")
    return out


def metrics(host, cfg, data, now):
    values = {}
    for alloc_id, entry in data["allocations"].items():
        labels = {"id": alloc_id, "user": entry["user"], "state": entry["state"]}
        values[key("scratch_alloc_limit_bytes", **labels)] = entry["bytes"]
        values[key("scratch_alloc_used_bytes", **labels)] = used_bytes(host, cfg["mount"], alloc_id)
        values[key("scratch_alloc_expires_timestamp_seconds", **labels)] = entry["expires"]
    values[key("scratch_alloc_last_success_timestamp_seconds")] = int(now)
    return render(values)


USAGE = __doc__.split("\n\n")[1]


def main(argv=None):
    args = (sys.argv if argv is None else argv)[1:]
    arity = {"create": (3, 4), "resize": (2,), "extend": (2,), "release": (1,), "list": (0, 1), "sweep": (0,),
             "metrics": (0,)}  # fmt: skip
    if len(args) < 2 or args[1] not in arity or len(args) - 2 not in arity[args[1]]:
        print(USAGE, file=sys.stderr)
        return 2

    def body():
        cfg = json.loads(Path(args[0]).read_text())
        host, now, op, rest = Host(), time.time(), args[1], args[2:]
        try:
            check_mount(host, cfg["mount"])
            with Registry(cfg["registry"], cfg.get("first_id", 1000)).open() as data:
                if op == "create":
                    result = create(host, cfg, data, rest[0], rest[1], rest[2], " ".join(rest[3:]), now)
                elif op == "resize":
                    result = resize(host, cfg, data, rest[0], rest[1])
                elif op == "extend":
                    result = extend(host, cfg, data, rest[0], rest[1], now)
                elif op == "release":
                    result = release(host, cfg, data, rest[0], now)
                elif op == "list":
                    result = [e | {"id": int(i)} for i, e in sorted(data["allocations"].items())
                              if not rest or e["user"] == rest[0]]  # fmt: skip
                elif op == "sweep":
                    result = sweep(host, cfg, data, now)
                else:
                    if cfg.get("textfile"):
                        write_atomic(cfg["textfile"], metrics(host, cfg, data, now))
                    return 0
        except Error as e:
            print(f"scratch-alloc: {e}", file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True))
        return 0

    return run_main("scratch-alloc", body)


if __name__ == "__main__":
    sys.exit(main())
