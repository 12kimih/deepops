"""disk_quota: per-user and per-directory (project) disk quotas, and their usage as metrics.

    disk-quota CONFIG.json apply [--check]   set them up; --check prints the steps only
    disk-quota CONFIG.json remove [--check]  undo it: limits, quotas, the quota file
    disk-quota CONFIG.json metrics           usage and limits (textfile)
    disk-quota CONFIG.json scan              the folders worth cleaning (textfile, weekly)

Each step is printed as it is taken (or would be, with --check), one line each. CONFIG
"-" is read from standard input (a preview of a configuration not written yet):

    {"filesystems": {"/": {},
                     "/home": {"alice": {"block_hard": "10T"}}},
     "release": ["alice", "bob"],
     "warn_fraction": 0.9, "inodes_per_tib": 3000000,
     "projects": {"/home/shared/datasets": {"filesystem": "/home", "id": 101,
                                            "block_hard": "20T"}},
     "textfile": "/run/prometheus/disk-quota.prom"}

Each filesystem has its own people's limits ({} counts only). Every name in release
that a filesystem does not limit has whatever limit it had there lifted (the roster:
someone left, or limited no longer).

scan walks each filesystem of "scan" ({filesystem: [base glob, ...]}, e.g. each home and
each collaboration folder) and writes the scan_top folders most worth cleaning, unused
past stale_days, to scan_textfile (cluster_tools.cleanup), at low priority: a home
server's walk takes hours, so it runs weekly.

A limit is its hard limits: block_hard, and inode_hard, or else block_hard's TiB times
inodes_per_tib (0: none), so the file count follows the space. The hard limit is the
only one the kernel enforces: a write past it fails at once. Each has its warn level,
warn_fraction of it, never sent to the kernel (its soft limit stays 0, so there is no
grace period that turns into a refusal later); it is exported with the usage, for the
alerts and the people's notices.

User quotas are on ext4 and XFS. An ext4 filesystem with its quota feature (tune2fs -O
quota) needs usrquota in its mount options; one without it keeps journaled quota files
(usrjquota=aquota.user,jqfmt=vfsv1, created by quotacheck), which can be turned on while
it is mounted. XFS keeps quotas in its metadata, on from the mount with usrquota
(uquota): XFS turns them on only at mount time, never on a remount, so adding the option
means unmounting it once. Project quotas (a directory tree's total) are on ext4 with the project
feature and prjquota, or on XFS with prjquota (pquota). The mount options are not this
tool's to write: /etc/fstab has one writer (roles/host_baseline,
host_baseline_mount_options), which also remounts, so a filesystem mounted without them
is refused, saying which to add. Accounts with no entry in limits (root, system
accounts) are never limited.

Python 3.9 or newer, standard library only.
"""

import csv
import glob
import io
import os
import pwd
import re
import sys
import time
from pathlib import Path

from . import cleanup, config, proc
from .cli import run_main
from .files import write_atomic
from .prom import key, render

MOUNTS = "/proc/self/mounts"
JOURNALED = ["usrjquota=aquota.user", "jqfmt=vfsv1"]
FEATURE = ["usrquota"]
# The mount options that turn XFS user quotas on (/proc/self/mounts says usrquota).
XFS_USER_OPTIONS = ("usrquota", "uquota", "quota", "uqnoenforce")
QUOTA_FILE = "aquota.user"
# The mount options that turn project quotas on, per filesystem type.
PROJECT_OPTIONS = {"ext4": ("prjquota",), "xfs": ("prjquota", "pquota", "pqnoenforce")}
_UNITS = {"": 1, "K": 1, "M": 1024, "G": 1024**2, "T": 1024**3}
_INODE_UNITS = {"": 1, "k": 10**3, "m": 10**6, "g": 10**9, "t": 10**12}
TIMEOUT = 600
# quotacheck and chattr -R read every inode of a filesystem or tree: hours for hundreds
# of millions of files (a home server), so they get a day, at the lowest best-effort I/O
# priority and a low CPU one: behind what people are doing, yet always moving (the idle
# I/O class gets the disk only when nothing else wants it, never on a busy NFS server).
SCAN_TIMEOUT = 86400
LOW = ["nice", "-n", "10", "ionice", "-c", "2", "-n", "7"]


class Error(Exception):
    """A configuration or a host the tool refuses, said plainly."""


def kib(size):
    """SIZE ("20G", "512M", 1048576) in KiB, as setquota and repquota count blocks."""
    m = re.fullmatch(r"\s*(\d+)\s*([KMGT]?)\s*", str(size))
    if not m:
        raise Error(f"{size!r} is not a size (a number of KiB, or with K, M, G or T)")
    return int(m.group(1)) * _UNITS[m.group(2)]


def inodes(count):
    m = re.fullmatch(r"\s*(\d+)\s*([kmgt]?)\s*", str(count))
    if not m:
        raise Error(f"{count!r} is not an inode count (a number, or with k, m, g or t)")
    return int(m.group(1)) * _INODE_UNITS[m.group(2)]


LIMIT_KEYS = ("block_hard", "inode_hard")
TIB_KIB = 1024**3


def levels(name, lim, cfg):
    """(block_warn, block_hard, inode_warn, inode_hard) of LIM in KiB and inodes: the
    hard limits, the inodes from the blocks where LIM names none (cfg's inodes_per_tib),
    each warn level cfg's warn_fraction of its hard limit."""
    unknown = sorted(set(lim) - set(LIMIT_KEYS))
    if unknown:
        raise Error(
            f"{name}: {', '.join(unknown)}: a limit is {' and '.join(LIMIT_KEYS)};"
            " each warn level is warn_fraction of its hard limit"
        )
    block = kib(lim.get("block_hard", 0))
    count = (
        inodes(lim["inode_hard"]) if "inode_hard" in lim else block * int(cfg["inodes_per_tib"]) // TIB_KIB
    )
    fraction = float(cfg["warn_fraction"])
    return int(block * fraction), block, int(count * fraction), count


def wanted(limits, cfg):
    """{user: (block_warn, block_hard, inode_warn, inode_hard)}."""
    return {user: levels(user, lim, cfg) for user, lim in (limits or {}).items()}


def kernel(values):
    """What the kernel gets of (warn, hard, warn, hard): the hard limits, soft 0."""
    return 0, values[1], 0, values[3]


def _unescape(field):
    """A mount table field: spaces and tabs are written as octal escapes."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def mounts(text):
    """{mount point: (device, fstype, [options])} from /proc/self/mounts; the last of
    several mounts on one point is the one in use."""
    out = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 4:
            out[_unescape(fields[1])] = (_unescape(fields[0]), fields[2], fields[3].split(","))
    return out


def has_feature(tune2fs_output):
    """Whether tune2fs -l reports ext4's quota feature."""
    for line in tune2fs_output.splitlines():
        if line.startswith("Filesystem features:"):
            return "quota" in line.split(":", 1)[1].split()
    return False


def report(text):
    """{name: (block_used, block_soft, block_hard, inode_used, inode_soft, inode_hard)}
    from repquota -u|-P -v -O csv (blocks in KiB); a name repquota cannot resolve (a
    project without /etc/projid) comes as its number."""
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        name = (row.get("User") or row.get("Project") or row.get("Name") or "").lstrip("#")
        if not name:
            continue
        out[name] = tuple(
            int(row.get(col) or 0)
            for col in (
                "BlockUsed",
                "BlockSoftLimit",
                "BlockHardLimit",
                "FileUsed",
                "FileSoftLimit",
                "FileHardLimit",
            )
        )
    return out


class Host:
    """The commands and files the tool reads and changes: replaced in the tests."""

    def read(self, path):
        return Path(path).read_text()

    def exists(self, path):
        return os.path.exists(path)

    def run(self, argv, check=True, timeout=TIMEOUT):
        return proc.run(argv, timeout=timeout, capture_output=True, text=True, check=check).stdout

    def scan(self, argv):
        """ARGV, a command that reads a whole filesystem or tree: low priority, a day."""
        return self.run(LOW + list(argv), timeout=SCAN_TIMEOUT)

    def xfs_quota(self, mount, cmd):
        return self.run(["xfs_quota", "-x", "-c", cmd, mount])

    def remove(self, path):
        os.unlink(path)

    def lstat(self, path):
        return os.lstat(path)

    def scandir(self, path):
        return os.scandir(path)

    def glob(self, pattern):
        return sorted(p for p in glob.glob(pattern) if os.path.isdir(p) and not os.path.islink(p))

    def owner(self, uid):
        try:
            return pwd.getpwuid(uid).pw_name
        except KeyError:
            return str(uid)


def _limits_of(entry):
    return entry[1], entry[2], entry[4], entry[5]


def _mounted(host, mount):
    table = mounts(host.read(MOUNTS))
    if mount not in table:
        raise Error(f"{mount} is not mounted")
    return table[mount]


def _refuse_options(mount, options):
    raise Error(
        f"{mount} is mounted without {options}: add it to its entry in"
        f" host_baseline_mount_options and run playbooks/generic/host-baseline.yml"
        f" (it sets /etc/fstab and remounts), then run this again"
    )


def quota_options(feature):
    """The mount options per-user quotas need: usrquota with ext4's quota feature,
    journaled quota files without it."""
    return FEATURE if feature else JOURNALED


def filesystem_steps(host, mount, cfg):
    """[(description, action)] that turn MOUNT's per-user quotas on, in order."""
    device, fstype, live = _mounted(host, mount)
    if fstype == "xfs":
        # On from the mount itself: nothing to create or turn on.
        if not set(live) & set(XFS_USER_OPTIONS):
            _refuse_options(mount, "usrquota")
        return []
    if fstype != "ext4":
        raise Error(f"{mount} is {fstype}; user quotas are set on ext4 and xfs")
    feature = has_feature(host.run(["tune2fs", "-l", device]))
    options = quota_options(feature)
    if not all(o in live for o in options):
        _refuse_options(mount, ",".join(options))
    steps = []
    off = not re.search(r"\bis on\b", host.run(["quotaon", "-pu", mount], check=False))
    # Journaled quota files are kept current only while quotas are on: whatever was
    # written while they were off is not in them, an old count included (a quotacheck
    # finished hours before the quotaon, 2026-11-23). So every turning on counts afresh
    # first; with ext4's quota feature the kernel keeps the counts itself.
    if not feature and off:
        check = ["quotacheck", "-cum", mount]
        steps.append((" ".join(check), lambda: host.scan(check)))
    # quotaon on a filesystem whose quotas are on already fails: asked only when off.
    if off:
        on = ["quotaon", "-u", mount]
        steps.append((" ".join(on), lambda: host.run(on)))
    return steps


def limit_steps(host, mount, cfg, fresh=False):
    """[(description, action)] that set every user's hard limits on MOUNT (soft 0)."""
    want = wanted(cfg["filesystems"][mount], cfg)
    have = {} if fresh else report(host.run(["repquota", "-u", "-v", "-O", "csv", mount]))
    steps = []
    for user, values in sorted(want.items()):
        if user not in have or _limits_of(have[user]) != kernel(values):
            argv = ["setquota", "-u", user] + [str(v) for v in kernel(values)] + [mount]
            steps.append((" ".join(argv), lambda a=argv: host.run(a)))
    for user in sorted(set(cfg.get("release") or []) - set(want)):
        if user in have and any(_limits_of(have[user])):
            argv = ["setquota", "-u", user, "0", "0", "0", "0", mount]
            steps.append((" ".join(argv), lambda a=argv: host.run(a)))
    return steps


# Project quotas: the deploy-time projects.


def require_project_quota(host, mount):
    """MOUNT's filesystem type, or Error unless it is mounted with project quotas."""
    _, fstype, live = _mounted(host, mount)
    if fstype not in PROJECT_OPTIONS:
        raise Error(f"{mount} is {fstype}; project quotas are set on ext4 and xfs")
    if not set(live) & set(PROJECT_OPTIONS[fstype]):
        _refuse_options(mount, "prjquota")
    return fstype


def project_of(host, path):
    """The project id PATH carries (lsattr -p reads it on ext4 and xfs)."""
    out = host.run(["lsattr", "-pd", path])
    m = re.match(r"\s*(\d+)\s", out)
    return int(m.group(1)) if m else None


def project_set(host, mount, path, pid):
    """Make PATH's tree project PID, new files there inheriting it."""
    if require_project_quota(host, mount) == "xfs":
        host.xfs_quota(mount, f"project -s -p {path} {pid}")
    else:
        host.scan(["chattr", "-R", "-p", str(pid), "+P", path])


def project_limit(host, mount, pid, hard_bytes, warn_bytes=0):
    """Project PID's hard limit (bytes; 0 none). The warn level never reaches the kernel:
    it is the caller's to export (warn_bytes is accepted for one signature)."""
    del warn_bytes
    hard = -(-int(hard_bytes) // 1024)  # whole KiB, rounded up
    if require_project_quota(host, mount) == "xfs":
        host.xfs_quota(mount, f"limit -p bsoft=0 bhard={hard}k {pid}")
    else:
        host.run(["setquota", "-P", str(pid), "0", str(hard), "0", "0", mount])


def _project_report(host, mount, fstype, pid):
    """(block_used, block_soft, block_hard) of project PID in KiB."""
    if fstype == "xfs":
        for line in host.xfs_quota(mount, f"quota -p -N -b {pid}").splitlines():
            fields = line.split()
            if len(fields) >= 4 and all(f.isdigit() for f in fields[1:4]):
                return int(fields[1]), int(fields[2]), int(fields[3])
        return 0, 0, 0
    row = report(host.run(["repquota", "-P", "-v", "-n", "-O", "csv", mount])).get(str(pid))
    return (row[0], row[1], row[2]) if row else (0, 0, 0)


def project_used(host, mount, pid):
    """Bytes project PID uses."""
    fstype = require_project_quota(host, mount)
    return _project_report(host, mount, fstype, pid)[0] * 1024


def _project_limits(proj):
    """A project's limit: the space alone (its files are not counted per project)."""
    return {k: v for k, v in proj.items() if k not in ("filesystem", "id")}


def project_steps(host, cfg):
    """[(description, action)] that make each configured project's tree and limit."""
    steps = []
    on_checked = set()
    for path, proj in sorted((cfg.get("projects") or {}).items()):
        mount, pid = proj["filesystem"], int(proj["id"])
        warn, hard, _, _ = levels(path, _project_limits(proj), cfg)
        fstype = require_project_quota(host, mount)
        if fstype == "ext4" and mount not in on_checked:
            on_checked.add(mount)
            if not re.search(r"\bis on\b", host.run(["quotaon", "-pP", mount], check=False)):
                on = ["quotaon", "-P", mount]
                steps.append((" ".join(on), lambda a=on: host.run(a)))
        if project_of(host, path) != pid:
            steps.append(
                (f"make {path} project {pid}", lambda m=mount, p=path, i=pid: project_set(host, m, p, i))
            )
        if _project_report(host, mount, fstype, pid)[1:] != (0, hard):
            steps.append(
                (
                    f"limit project {pid} ({path}) to {hard} KiB",
                    lambda m=mount, i=pid, h=hard * 1024, w=warn * 1024: project_limit(host, m, i, h, w),
                )
            )
    return steps


def remove_steps(host, mount, cfg):
    """[(description, action)] that take MOUNT's user quotas away: the limits, the
    quotas and the quota file. The mount options are host_baseline_mount_options' to
    drop: with them left, the next boot turns quotas on again."""
    steps = []
    _, fstype, _ = _mounted(host, mount)
    if fstype == "xfs":
        # Its quotas go with the mount option; the limits are lifted here.
        have = report(host.run(["repquota", "-u", "-v", "-O", "csv", mount]))
        for user in sorted(set(cfg["filesystems"][mount]) | set(cfg.get("release") or [])):
            if user in have and any(_limits_of(have[user])):
                argv = ["setquota", "-u", user, "0", "0", "0", "0", mount]
                steps.append((" ".join(argv), lambda a=argv: host.run(a)))
        return steps
    if re.search(r"\bis on\b", host.run(["quotaon", "-pu", mount], check=False)):
        have = report(host.run(["repquota", "-u", "-v", "-O", "csv", mount]))
        for user in sorted(set(cfg["filesystems"][mount]) | set(cfg.get("release") or [])):
            if user in have and any(_limits_of(have[user])):
                argv = ["setquota", "-u", user, "0", "0", "0", "0", mount]
                steps.append((" ".join(argv), lambda a=argv: host.run(a)))
        off = ["quotaoff", "-u", mount]
        steps.append((" ".join(off), lambda: host.run(off)))
    quota_file = os.path.join(mount, QUOTA_FILE)
    if host.exists(quota_file):
        steps.append((f"remove {quota_file}", lambda: host.remove(quota_file)))
    return steps


def remove_project_steps(host, cfg):
    """Each configured project's limit lifted; its tree and data stay."""
    steps = []
    for path, proj in sorted((cfg.get("projects") or {}).items()):
        mount, pid = proj["filesystem"], int(proj["id"])
        try:
            fstype = require_project_quota(host, mount)
        except Error:
            continue  # quotas already off there: no limit left to lift
        if any(_project_report(host, mount, fstype, pid)[1:]):
            steps.append(
                (
                    f"lift the limit of project {pid} ({path})",
                    lambda m=mount, i=pid: project_limit(host, m, i, 0),
                )
            )
    return steps


def metrics(host, cfg, now):
    """Usage and limits, as textfile metrics: the hard limit as the kernel holds it, the
    warn level from the configuration (the kernel never has it)."""
    values = {}
    for mount, limits in (cfg.get("filesystems") or {}).items():
        want = wanted(limits, cfg)
        for user, (bu, _, bh, iu, _, ih) in report(
            host.run(["repquota", "-u", "-v", "-O", "csv", mount])
        ).items():
            labels = {"filesystem": mount, "user": user}
            values[key("disk_quota_used_bytes", **labels)] = bu * 1024
            values[key("disk_quota_used_inodes", **labels)] = iu
            bw, _, iw, _ = want.get(user, (0, 0, 0, 0))
            for kind, b, i in (("warn", bw, iw), ("hard", bh, ih)):
                if b:
                    values[key("disk_quota_limit_bytes", kind=kind, **labels)] = b * 1024
                if i:
                    values[key("disk_quota_limit_inodes", kind=kind, **labels)] = i
    for path, proj in sorted((cfg.get("projects") or {}).items()):
        mount, pid = proj["filesystem"], int(proj["id"])
        used, _, hard = _project_report(host, mount, require_project_quota(host, mount), pid)
        warn = levels(path, _project_limits(proj), cfg)[0]
        labels = {"filesystem": mount, "project": path, "project_id": str(pid)}
        values[key("disk_quota_project_used_bytes", **labels)] = used * 1024
        for kind, b in (("warn", warn), ("hard", hard)):
            if b:
                values[key("disk_quota_project_limit_bytes", kind=kind, **labels)] = b * 1024
    values[key("disk_quota_last_success_timestamp_seconds")] = int(now)
    return render(values)


def scan(host, cfg, now):
    """{key: value} of the folders worth cleaning on each filesystem of cfg's "scan"."""
    values = {}
    names = {}
    for filesystem, patterns in sorted((cfg.get("scan") or {}).items()):
        dev = host.lstat(filesystem).st_dev
        bases = [b for pattern in patterns for b in host.glob(pattern)]
        folders = cleanup.Folders(bases)
        budget = [int(cfg["scan_max_entries"])]
        for base in bases:
            for path, st in cleanup.walk(host, base, dev, set(), budget):
                if st.st_uid not in names:
                    names[st.st_uid] = host.owner(st.st_uid)
                folders.add(path, names[st.st_uid], st.st_size, cleanup.last_use(st))
        values.update(
            cleanup.values(filesystem, folders.ranked(now, int(cfg["stale_days"]), int(cfg["scan_top"])), now)
        )
    return values


def steps_for(host, cfg, action):
    out = []
    if action == "remove":
        out += remove_project_steps(host, cfg)
        for mount in cfg.get("filesystems") or {}:
            out += remove_steps(host, mount, cfg)
        return out
    for mount in cfg.get("filesystems") or {}:
        fs = filesystem_steps(host, mount, cfg)
        out += fs
        # Quotas not on yet (they are turned on above): nothing can be read, and every
        # limit is set.
        out += limit_steps(host, mount, cfg, fresh=any(t.startswith("quotaon ") for t, _ in fs))
    return out + project_steps(host, cfg)


USAGE = "usage: disk-quota CONFIG.json apply|remove [--check] | metrics | scan"

CONFIG_KEYS = {
    "filesystems": dict,
    "release": list,
    "projects": dict,
    "warn_fraction": float,
    "inodes_per_tib": int,
    "textfile": str,
    "scan": dict,
    "scan_textfile": str,
    "scan_top": int,
    "scan_max_entries": int,
    "stale_days": int,
}


def load_config(path):
    """The config roles/disk_quota writes (cluster_tools.config); "-" reads it from stdin."""
    return config.require(path, CONFIG_KEYS)


def main(argv=None):
    argv = sys.argv if argv is None else argv
    args = argv[1:]
    check = args[2:] == ["--check"]
    if not (len(args) == 2 or (check and len(args) == 3)) or args[1] not in (
        "apply",
        "remove",
        "metrics",
        "scan",
    ):
        print(USAGE, file=sys.stderr)
        return 2
    if check and args[1] in ("metrics", "scan"):
        print(USAGE, file=sys.stderr)
        return 2

    def body():
        cfg = load_config(args[0])
        host = Host()
        try:
            if args[1] == "metrics":
                if cfg.get("textfile"):
                    write_atomic(cfg["textfile"], metrics(host, cfg, time.time()))
                return 0
            if args[1] == "scan":
                if cfg.get("scan"):
                    write_atomic(cfg["scan_textfile"], render(scan(host, cfg, time.time())))
                return 0
            for text, action in steps_for(host, cfg, args[1]):
                print(text)
                if not check:
                    action()
        except Error as e:
            print(f"disk-quota: {e}", file=sys.stderr)
            return 1
        return 0

    return run_main("disk-quota", body)
