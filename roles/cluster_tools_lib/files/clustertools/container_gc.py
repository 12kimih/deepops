"""container_gc: remove users' container image stores from a node's local disk.

    container-storage-gc CONFIG.json [--dry-run]

Rootless podman (rootless_storage_path in storage.conf) and enroot (ENROOT_CACHE_PATH
in enroot.conf and enroot.conf.d) keep one store per user on local disk, and nothing
else ever shrinks them. Each store's path is read from the tool's own configuration,
so this follows whatever the roles wrote there. A user's whole store is removed when:

  left      the uid is not in active_uids (when the config gives that list)
  unused    nothing in it changed for unused_days
  full      the filesystem is above max_used_percent: idle stores go, least recently
            used first, until it is below

never while the uid runs one of the store's busy programs (checked again just before
each removal). A store is renamed away first, then recreated empty and owned by its
user where the config says so (a parent only root may write to), then deleted without
crossing a mount. Only a real directory owned by the uid its name carries is touched.
The run's result is written as node_exporter textfile metrics:

  container_storage_gc_last_success_timestamp_seconds
  container_storage_gc_removed{store,reason}   stores removed by this run
  container_storage_gc_busy{store}             stores spared because they were in use
  container_storage_gc_used_ratio{store}       the store filesystem's use after the run

With docker in the config ({socket, label}), the containers the node's dockerd holds
that deepops did not start (no `label`, which roles/container_service sets) are counted,
never touched: they run outside Slurm, its limits and its cleanup, so they are only
made visible. docker-prune.timer (roles/docker) removes them once stopped and old.

  container_storage_gc_docker_unmanaged{state}        running or stopped
  container_storage_gc_docker_unmanaged_stale{state}  of those, created more than
                                                   unused_days ago

Runs as root from a daily timer. Python 3.9 or newer, standard library only.
"""

import http.client
import json
import os
import re
import socket
import stat
import sys
import tempfile
import time
from pathlib import Path

from . import config, proc
from .cli import run_main
from .files import write_atomic
from .prom import series

UID_TOKENS = ("$(id -u)", "${UID}", "$UID")
REMOVE_TIMEOUT = 3600
DOCKER_TIMEOUT = 30
TRASH_PREFIX = ".gc-"


def log(msg):
    print(f"container-storage-gc: {msg}", file=sys.stderr)


def enroot_value(conf, key):
    """KEY's value as enroot reads it: enroot.conf, then enroot.conf.d/*.conf in name
    order, the last setting winning."""
    conf = Path(conf)
    files = [conf] + sorted(Path(str(conf) + ".d").glob("*.conf"))
    value = None
    for path in files:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            fields = line.strip().split(None, 1)
            if len(fields) == 2 and fields[0] == key:
                value = fields[1].strip()
    return value


def podman_value(conf):
    """rootless_storage_path from a storage.conf (its [storage] table)."""
    try:
        text = Path(conf).read_text()
    except OSError:
        return None
    table = None
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("["):
            table = line.strip("[] ")
        elif table == "storage":
            m = re.fullmatch(r'rootless_storage_path\s*=\s*"([^"]*)"', line)
            if m:
                return m.group(1)
    return None


def pattern(template):
    """(parent, regex) for a per-user path whose last component carries the uid, or None
    when it is not one (no uid, the uid above the last component, other variables)."""
    if not template or not template.startswith("/"):
        return None
    parent, name = os.path.split(template.rstrip("/"))
    if "$" in parent:
        return None
    for token in UID_TOKENS:
        if token in name:
            before, _, after = name.partition(token)
            if "$" in before + after:
                return None
            return parent, re.compile(re.escape(before) + r"([0-9]+)" + re.escape(after) + r"\Z")
    return None


def store_template(store):
    if store["kind"] == "podman":
        return podman_value(store["config"])
    if store["kind"] == "enroot":
        return enroot_value(store["config"], store.get("key", "ENROOT_CACHE_PATH"))
    raise ValueError(f"unknown store kind {store['kind']}")


def processes(proc_root="/proc"):
    """{uid: {comm, ...}} of every live process (a zombie holds nothing open)."""
    out = {}
    for entry in os.listdir(proc_root):
        if not entry.isdigit():
            continue
        try:
            status = Path(proc_root, entry, "status").read_text()
        except OSError:
            continue
        name = uid = None
        zombie = False
        for line in status.splitlines():
            if line.startswith("Name:"):
                name = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else ""
            elif line.startswith("Uid:"):
                uid = int(line.split()[1])
            elif line.startswith("State:"):
                zombie = line.split()[1:2] == ["Z"]
        if uid is not None and not zombie:
            out.setdefault(uid, set()).add(name or "")
    return out


def busy(uid, programs, procs):
    return any(c.startswith(tuple(programs)) for c in procs.get(uid, ()))


def last_used(path):
    """The newest use of PATH, its entries and theirs (no deeper: a store's lock files
    and databases sit there and change with every write). A file counts when it was
    written or read: an image only ever imported again (enroot reads its cached layers)
    is in use. A directory counts only when written: listing it here sets its access
    time. Under noatime a read is not seen, so only writes count, as before."""
    newest = 0.0
    level = [str(path)]
    for _ in range(3):
        nxt = []
        for p in level:
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                newest = max(newest, st.st_mtime)
                try:
                    nxt.extend(os.path.join(p, e) for e in os.listdir(p))
                except OSError:
                    continue
            else:
                newest = max(newest, st.st_mtime, st.st_atime)
        level = nxt
    return newest


def used_ratio(path):
    st = os.statvfs(path)
    used = st.f_blocks - st.f_bfree
    total = used + st.f_bavail
    return used / total if total else 0.0


def stores_found(parent, regex, uid_min):
    """[(uid, path, stat)] of the per-user directories under PARENT."""
    found = []
    try:
        names = os.listdir(parent)
    except OSError:
        return found
    for name in names:
        m = regex.match(name)
        if not m:
            continue
        uid = int(m.group(1))
        path = os.path.join(parent, name)
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if uid < uid_min or not os.path.isdir(path) or os.path.islink(path) or st.st_uid != uid:
            continue
        found.append((uid, path, st))
    return sorted(found)


def remove(path, st, recreate, dry_run):
    """Move PATH into a fresh trash directory beside it, recreate it empty for its user
    where RECREATE says, then delete the trash. The trash is made by mkdtemp: ours
    (this uid, mode 0700) under a name no one can guess, so a user who writes to the
    parent cannot take or block it. Whether the store went; a failure is logged and the
    run goes on with the next store."""
    if dry_run:
        return True
    parent = os.path.dirname(path)
    try:
        trash = tempfile.mkdtemp(prefix=TRASH_PREFIX, dir=parent)
        os.rename(path, os.path.join(trash, "store"))
        if recreate:
            os.mkdir(path, 0o700)
            os.chown(path, st.st_uid, st.st_gid)
            os.chmod(path, 0o700)
    except OSError as e:
        log(f"could not remove {path}: {e}")
        return False
    return remove_trash(trash)


def leftover_trash(parent):
    """The trash directories an interrupted run left in PARENT: only ours (this uid,
    mode 0700), never one a user made under the same prefix."""
    try:
        names = os.listdir(parent)
    except OSError:
        return []
    out = []
    for name in sorted(names):
        path = os.path.join(parent, name)
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if (
            name.startswith(TRASH_PREFIX)
            and stat.S_ISDIR(st.st_mode)
            and st.st_uid == os.geteuid()
            and stat.S_IMODE(st.st_mode) == 0o700
        ):
            out.append(path)
    return out


def remove_trash(trash):
    try:
        proc.run(["rm", "-rf", "--one-file-system", "--", trash], timeout=REMOVE_TIMEOUT)
    except (proc.CalledProcessError, proc.TimeoutExpired) as e:
        log(f"could not remove all of {trash}: {e}")
        return False
    return True


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTP over a unix socket: the Docker Engine API on dockerd's own socket."""

    def __init__(self, path):
        super().__init__("localhost", timeout=DOCKER_TIMEOUT)
        self._path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


def docker_containers(path):
    """Every container the dockerd at socket PATH holds (GET /containers/json?all=1), or
    None when no dockerd is there."""
    if not os.path.exists(path):
        return None
    conn = _UnixHTTPConnection(path)
    try:
        conn.request("GET", "/containers/json?all=1")
        response = conn.getresponse()
        body = response.read()
    finally:
        conn.close()
    if response.status != 200:
        raise OSError(f"{path}: the Docker API answered {response.status}")
    return json.loads(body)


def unmanaged(containers, label, stale_before):
    """The metric lines for CONTAINERS without LABEL: running or stopped, and how many
    of each were created before STALE_BEFORE (epoch seconds)."""
    counts = {(state, stale): 0 for state in ("running", "stopped") for stale in (False, True)}
    for container in containers:
        if label in (container.get("Labels") or {}):
            continue
        state = "running" if container.get("State") == "running" else "stopped"
        counts[(state, False)] += 1
        if container.get("Created", 0) < stale_before:
            counts[(state, True)] += 1
    return [
        series("container_storage_gc_docker_unmanaged_stale" if stale else "container_storage_gc_docker_unmanaged",
               {"state": state}, count)
        for (state, stale), count in sorted(counts.items())
    ]  # fmt: skip


def run(cfg, dry_run=False, now=None, proc_root="/proc"):
    """Collect every store in CFG; returns the metric lines and whether all went well."""
    now = time.time() if now is None else now
    active = set(cfg["active_uids"]) if cfg.get("active_uids") is not None else None
    unused = float(cfg["unused_days"]) * 86400
    limit = float(cfg["max_used_percent"]) / 100
    uid_min = int(cfg.get("uid_min", 1000))
    procs = processes(proc_root)
    verb = "would remove" if dry_run else "removing"
    lines, ok = [], True
    for store in cfg["stores"]:
        name, programs = store["name"], store["busy"]
        found = pattern(store_template(store))
        if not found:
            log(f"{name}: no per-user path in {store['config']}; skipped")
            continue
        parent, regex = found
        for leftover in () if dry_run else leftover_trash(parent):
            remove_trash(leftover)
        removed = {"left": 0, "unused": 0, "full": 0}
        spared = 0
        idle = []
        for uid, path, st in stores_found(parent, regex, uid_min):
            if busy(uid, programs, procs):
                spared += 1
                continue
            reason = None
            if active is not None and uid not in active:
                reason = "left"
            elif now - last_used(path) > unused:
                reason = "unused"
            if reason is None:
                idle.append((last_used(path), uid, path, st))
                continue
            if busy(uid, programs, processes(proc_root)):
                spared += 1
                continue
            recreate = store.get("recreate", False) and reason != "left"
            log(f"{name}: {verb} {path} ({reason})")
            if remove(path, st, recreate, dry_run):
                removed[reason] += 1
            else:
                ok = False
        for _, uid, path, st in sorted(idle):
            if dry_run or used_ratio(parent) <= limit:
                break
            if busy(uid, programs, processes(proc_root)):
                spared += 1
                continue
            log(f"{name}: {verb} {path} (filesystem above {cfg['max_used_percent']}%)")
            if remove(path, st, store.get("recreate", False), dry_run):
                removed["full"] += 1
            else:
                ok = False
        for reason, count in sorted(removed.items()):
            lines.append(series("container_storage_gc_removed", {"store": name, "reason": reason}, count))
        lines.append(series("container_storage_gc_busy", {"store": name}, spared))
        if os.path.isdir(parent):
            lines.append(
                series("container_storage_gc_used_ratio", {"store": name}, round(used_ratio(parent), 4))
            )
    if cfg.get("docker"):
        try:
            containers = docker_containers(cfg["docker"]["socket"])
        except (OSError, ValueError) as e:  # dockerd down: no count, the cleanup still ran
            log(f"docker: {e}")
            containers = None
        if containers is not None:
            lines += unmanaged(containers, cfg["docker"]["label"], now - unused)
    return lines, ok


def textfile(lines, ok, now):
    out = list(lines)
    if ok:
        out.append(f"container_storage_gc_last_success_timestamp_seconds {int(now)}")
    return "".join(line + "\n" for line in sorted(out))


CONFIG_KEYS = {"textfile": str, "unused_days": int, "max_used_percent": int,
               "active_uids": (list, type(None)), "stores": list}  # fmt: skip
CONFIG_OPTIONAL = {"uid_min": int, "docker": dict}
DOCKER_KEYS = ("socket", "label")


def load_config(path):
    """The config roles/container_storage_gc writes (clustertools.config)."""
    cfg = config.require(path, CONFIG_KEYS, CONFIG_OPTIONAL)
    docker = cfg.get("docker")
    if docker is not None and (
        sorted(docker) != sorted(DOCKER_KEYS)
        or not all(isinstance(docker[k], str) and docker[k] for k in DOCKER_KEYS)
    ):
        raise config.ConfigError(f"{path}: docker takes exactly {', '.join(DOCKER_KEYS)}, each a string")
    return cfg


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) not in (2, 3) or (len(argv) == 3 and argv[2] != "--dry-run"):
        print("usage: container-storage-gc CONFIG.json [--dry-run]", file=sys.stderr)
        return 2
    dry_run = len(argv) == 3

    def body():
        cfg = load_config(argv[1])
        now = time.time()
        lines, ok = run(cfg, dry_run, now)
        if not dry_run and cfg.get("textfile"):
            if ok:
                write_atomic(cfg["textfile"], textfile(lines, ok, now))
            else:  # keep the last success: the staleness alert then fires
                old = Path(cfg["textfile"]).read_text() if os.path.exists(cfg["textfile"]) else ""
                kept = [
                    line for line in old.splitlines() if line.startswith("container_storage_gc_last_success")
                ]
                write_atomic(cfg["textfile"], "".join(line + "\n" for line in sorted(lines + kept)))
        return 0 if ok else 1

    return run_main("container-storage-gc", body)


if __name__ == "__main__":
    sys.exit(main())
