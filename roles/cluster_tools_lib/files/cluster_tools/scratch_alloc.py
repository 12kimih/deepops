"""scratch_alloc: space on the fast scratch filesystem, one allocation per person, asked
for: /scratch/<login>, with a size limit (a project quota) and an end date.

    scratch-alloc CONFIG.json create LOGIN SIZE DAYS [REASON]  LOGIN's allocation
    scratch-alloc CONFIG.json resize LOGIN SIZE                a new size limit
    scratch-alloc CONFIG.json extend LOGIN DAYS                DAYS more past its end date
    scratch-alloc CONFIG.json release LOGIN                    given back now
    scratch-alloc CONFIG.json list [LOGIN]                     the allocations and the disk, as JSON
    scratch-alloc CONFIG.json sweep                            the end dates kept (a timer)
    scratch-alloc CONFIG.json metrics                          each allocation's size and use
    scratch-alloc CONFIG.json serve                            one of the above, from SSH

serve is the forced command of the cluster bot's SSH key: it takes the request from
SSH_ORIGINAL_COMMAND (create, resize, extend, release or list, with their arguments,
nothing else) and runs it as above.

Run as root on the host whose filesystem holds the scratch space. A person has at most
one allocation, the directory <mount>/<login>, theirs alone (0700), whose project quota
(its ID is the person's UID, as unique on the filesystem as the account is) is its
size. A directory already there that is theirs is taken as it is, files and all. The
registry (a JSON file, written atomically under a lock) records each allocation, so a
request is applied the moment it is accepted.

Hard caps, the configuration's, whoever asks: a size of max_bytes, an end at most
max_days after the allocation was made (an extension adds at most max_extend_days).
What a request gets without an administrator is the cluster bot's to decide.

Every sweep adds to each person's ledger the bytes allocated and the bytes used, times
the time since the last sweep: their cumulative byte-seconds from their first allocation,
kept after an allocation goes, so who has held how much for how long is known for good
(the fairness an administrator weighs when the disk is full, and the daily report).

At its end date an allocation is frozen: its limit drops to what it holds, so nothing
more can be written but everything can be read and copied away. After grace_days it is
removed. A released one is moved aside at once and removed by the next sweep, so
release never waits on a large delete.

CONFIG: {"mount": "/scratch", "registry": "/var/lib/scratch-alloc/registry.json",
         "max_bytes": "10T", "max_days": 180, "max_extend_days": 90, "grace_days": 14,
         "textfile": "/run/prometheus/scratch-alloc.prom"}
Python 3.9 or newer, standard library only.
"""

import fcntl
import json
import os
import pwd
import shutil
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from . import bot_serve, config, disk_quota
from .cli import run_main
from .files import write_atomic
from .prom import key, render

TRASH = ".released"
DAY = 86400


class Error(Exception):
    """A request or a host the tool refuses, said plainly."""


class Host(disk_quota.Host):
    """The commands and files the tool reads and changes: replaced in the tests. Project
    quotas go through cluster_tools.disk_quota (its Host: read, run, xfs_quota)."""

    def make_dir(self, path, uid, gid):
        os.mkdir(path, 0o700)
        os.chown(path, uid, gid)
        os.chmod(path, 0o700)

    def owner(self, path):
        """(uid, is a directory and no link) of PATH, or None when there is nothing."""
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return None
        return st.st_uid, stat.S_ISDIR(st.st_mode)

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

    def disk(self, mount):
        """(bytes, used bytes) of the filesystem at MOUNT."""
        st = os.statvfs(mount)
        return st.f_blocks * st.f_frsize, (st.f_blocks - st.f_bfree) * st.f_frsize


def nbytes(size):
    try:
        return disk_quota.kib(size) * 1024
    except disk_quota.Error as e:
        raise Error(str(e)) from None


def check_mount(host, mount):
    """The scratch filesystem has project quotas on (XFS or ext4), or nothing can be limited."""
    try:
        disk_quota.require_project_quota(host, mount)
    except disk_quota.Error as e:
        raise Error(str(e)) from None


class Registry:
    """The allocations, by login, read and written whole under an exclusive lock."""

    def __init__(self, path):
        self.path = path

    @contextmanager
    def open(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        with open(self.path + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = json.loads(Path(self.path).read_text())
            except FileNotFoundError:
                data = {"allocations": {}}
            data.setdefault("ledger", {})
            yield data
            write_atomic(self.path, json.dumps(data, indent=1, sort_keys=True) + "\n", mode=0o600)


def used_bytes(host, mount, pid):
    return disk_quota.project_used(host, mount, pid)


def set_limit(host, mount, pid, limit):
    """The project's hard limit; no kernel soft limit or grace period (warnings are Slack's)."""
    disk_quota.project_limit(host, mount, pid, max(limit, 0))


def find(data, login):
    entry = data["allocations"].get(login)
    if not entry:
        raise Error(f"{login} has no allocation")
    return entry


def _size(cfg, size):
    limit = nbytes(size)
    if limit <= 0 or limit > nbytes(cfg["max_bytes"]):
        raise Error(f"{size} is outside 1 KiB to {cfg['max_bytes']}")
    return limit


def create(host, cfg, data, login, size, days, reason, now):
    """LOGIN's allocation: <mount>/<login>, made, or taken as it is when theirs already."""
    if login in data["allocations"]:
        raise Error(f"{login} has an allocation already: resize or extend it")
    ids = host.user(login)
    if not ids:
        raise Error(f"no account {login!r}")
    limit, days = _size(cfg, size), int(days)
    if not 0 < days <= cfg["max_days"]:
        raise Error(f"{days} days is outside 1 to {cfg['max_days']}")
    path = os.path.join(cfg["mount"], login)
    there = host.owner(path)
    if there is None:
        host.make_dir(path, *ids)
    elif there != (ids[0], True):
        raise Error(f"{path} is there and not {login}'s own directory")
    disk_quota.project_set(host, cfg["mount"], path, ids[0])
    set_limit(host, cfg["mount"], ids[0], limit)
    data["allocations"][login] = {
        "user": login, "uid": ids[0], "path": path, "bytes": limit, "created": int(now),
        "expires": int(now + days * DAY), "state": "active", "reason": reason,
    }  # fmt: skip
    return data["allocations"][login]


def resize(host, cfg, data, login, size):
    entry = find(data, login)
    if entry["state"] != "active":
        raise Error(f"{login}'s allocation is {entry['state']}: extend it first")
    entry["bytes"] = _size(cfg, size)
    set_limit(host, cfg["mount"], entry["uid"], entry["bytes"])
    return entry


def extend(host, cfg, data, login, days, now):
    """DAYS more past its end date (or today once that passed), at most max_extend_days at
    a time and never past max_days from when it was made; a frozen allocation gets its
    size limit back."""
    entry = find(data, login)
    if entry["state"] == "released":
        raise Error(f"{login}'s allocation was released")
    days = int(days)
    if not 0 < days <= cfg["max_extend_days"]:
        raise Error(f"an extension adds 1 to {cfg['max_extend_days']} days")
    end = max(entry["expires"], now) + days * DAY
    if end > entry["created"] + cfg["max_days"] * DAY:
        raise Error(f"an allocation lasts at most {cfg['max_days']} days from when it was made")
    if entry["state"] == "frozen":
        set_limit(host, cfg["mount"], entry["uid"], entry["bytes"])
        entry["state"] = "active"
    entry["expires"] = int(end)
    return entry


def release(host, cfg, data, login, now):
    """Given back now: out of the person's sight at once, removed by the next sweep."""
    entry = find(data, login)
    if entry["state"] == "released":
        return entry
    if host.exists(entry["path"]):
        host.rename(entry["path"], os.path.join(cfg["mount"], TRASH, f"{login}-{int(now)}"))
    entry["state"], entry["released"] = "released", int(now)
    return entry


def account(host, cfg, data, now):
    """Add to each person's ledger what they held and used since the last sweep (since the
    allocation was made, the first time): byte-seconds, allocated and used."""
    for login, entry in sorted(data["allocations"].items()):
        if entry["state"] == "released":
            continue
        book = data.setdefault("ledger", {}).setdefault(
            login, {"allocated_byte_seconds": 0, "used_byte_seconds": 0, "since": entry["created"]}
        )
        last = max(book.get("last", entry["created"]), entry["created"])
        seconds = max(0, int(now - last))
        book["allocated_byte_seconds"] += entry["bytes"] * seconds
        book["used_byte_seconds"] += used_bytes(host, cfg["mount"], entry["uid"]) * seconds
        book["last"] = int(now)


def sweep(host, cfg, data, now):
    """Account what each holds (account()), freeze what reached its end date, remove what
    stayed frozen past the grace period and what was released; each change as a line."""
    account(host, cfg, data, now)
    out, grace = [], cfg["grace_days"] * DAY
    for login, entry in sorted(data["allocations"].items()):
        if entry["state"] == "active" and now >= entry["expires"]:
            set_limit(
                host, cfg["mount"], entry["uid"], max(used_bytes(host, cfg["mount"], entry["uid"]), 4096)
            )
            entry["state"], entry["frozen"] = "frozen", int(now)
            out.append(f"froze {login}: ended, read-only for {cfg['grace_days']} days")
        elif entry["state"] == "frozen" and now >= entry["expires"] + grace:
            out.append(f"released {login}: grace period over")
            release(host, cfg, data, login, now)
        if entry["state"] == "released":
            trash = os.path.join(cfg["mount"], TRASH, f"{login}-{entry['released']}")
            if host.exists(trash):
                host.remove_tree(trash)
            # Its files are gone with it, so the project only needs its limit cleared.
            set_limit(host, cfg["mount"], entry["uid"], 0)
            del data["allocations"][login]
            out.append(f"removed {login}")
    return out


def listing(host, cfg, data, login=None):
    """The allocations and the ledger (LOGIN's alone, when given) and the disk: what the
    bot decides with."""
    size, used = host.disk(cfg["mount"])
    return {
        "allocations": [e for n, e in sorted(data["allocations"].items()) if login in (None, n)],
        "ledger": {n: b for n, b in sorted(data.get("ledger", {}).items()) if login in (None, n)},
        "disk": {"bytes": size, "used": used},
    }


def metrics(host, cfg, data, now):
    values = {}
    for login, entry in data["allocations"].items():
        labels = {"user": login, "state": entry["state"]}
        values[key("scratch_alloc_limit_bytes", **labels)] = entry["bytes"]
        values[key("scratch_alloc_used_bytes", **labels)] = used_bytes(host, cfg["mount"], entry["uid"])
        values[key("scratch_alloc_expires_timestamp_seconds", **labels)] = entry["expires"]
    for login, book in data.get("ledger", {}).items():
        values[key("scratch_alloc_allocated_byte_seconds_total", user=login)] = book["allocated_byte_seconds"]
        values[key("scratch_alloc_used_byte_seconds_total", user=login)] = book["used_byte_seconds"]
    size, used = host.disk(cfg["mount"])
    values[key("scratch_alloc_disk_bytes")] = size
    values[key("scratch_alloc_disk_used_bytes")] = used
    values[key("scratch_alloc_last_success_timestamp_seconds")] = int(now)
    return render(values)


USAGE = __doc__.split("\n\n")[1]


ARITY = {"create": (3, 4), "resize": (2,), "extend": (2,), "release": (1,), "list": (0, 1), "sweep": (0,),
         "metrics": (0,)}  # fmt: skip
# What the bot's SSH key may ask for: never sweep or metrics (the timer's).
SERVED = {"create", "resize", "extend", "release", "list"}


def served(config, original):
    """The argv a request over SSH (SSH_ORIGINAL_COMMAND) stands for, or an Error."""
    try:
        words = bot_serve.request(original, {op: ARITY[op] for op in SERVED})
    except bot_serve.Error as e:
        raise Error(str(e)) from None
    return ["scratch-alloc", config, *words]


CONFIG_KEYS = {"mount": str, "registry": str, "max_bytes": (int, str), "max_days": int, "max_extend_days": int,
               "grace_days": int, "textfile": str}  # fmt: skip


def load_config(path):
    """The config roles/scratch_alloc writes (cluster_tools.config)."""
    return config.require(path, CONFIG_KEYS)


def main(argv=None):
    args = (sys.argv if argv is None else argv)[1:]
    if len(args) == 2 and args[1] == "serve":
        try:
            return main(served(args[0], os.environ.get("SSH_ORIGINAL_COMMAND")))
        except Error as e:
            print(f"scratch-alloc: {e}", file=sys.stderr)
            return 2
    if len(args) < 2 or args[1] not in ARITY or len(args) - 2 not in ARITY[args[1]]:
        print(USAGE, file=sys.stderr)
        return 2

    def body():
        cfg = load_config(args[0])
        host, now, op, rest = Host(), time.time(), args[1], args[2:]
        try:
            check_mount(host, cfg["mount"])
            with Registry(cfg["registry"]).open() as data:
                if op == "create":
                    result = create(host, cfg, data, rest[0], rest[1], rest[2], " ".join(rest[3:]), now)
                elif op == "resize":
                    result = resize(host, cfg, data, rest[0], rest[1])
                elif op == "extend":
                    result = extend(host, cfg, data, rest[0], rest[1], now)
                elif op == "release":
                    result = release(host, cfg, data, rest[0], now)
                elif op == "list":
                    result = listing(host, cfg, data, rest[0] if rest else None)
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
