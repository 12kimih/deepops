"""disk_quota: per-user and per-directory (project) disk quotas, and their usage as metrics.

    disk-quota CONFIG.json apply [--check]   set them up; --check prints the steps only
    disk-quota CONFIG.json remove [--check]  undo it: limits, quotas, the quota file
    disk-quota CONFIG.json metrics           usage and limits (textfile)

Each step is printed as it is taken (or would be, with --check), one line each. CONFIG
"-" is read from standard input (a preview of a configuration not written yet):

    {"filesystems": ["/"],
     "limits": {"alice": {"block_warn": "10G", "block_hard": "20G",
                          "inode_warn": "200k", "inode_hard": "500k"}},
     "release": ["bob"],
     "projects": {"/home/shared/datasets": {"filesystem": "/home", "id": 101,
                                            "block_warn": "15T", "block_hard": "20T"}},
     "textfile": "/run/prometheus/disk-quota.prom"}

A limit has two levels. The hard limit is the only one the kernel enforces: a write past
it fails at once. The warn level is never sent to the kernel (its soft limit stays 0, so
there is no grace period that turns into a refusal later); it is exported with the
usage, for the alerts and the people's notices.

User quotas are on ext4. A filesystem with ext4's quota feature (tune2fs -O quota) needs
usrquota in its mount options; one without it keeps journaled quota files
(usrjquota=aquota.user,jqfmt=vfsv1, created by quotacheck), which can be turned on while
it is mounted. Project quotas (a directory tree's total) are on ext4 with the project
feature and prjquota, or on XFS with prjquota (pquota). The mount options are not this
tool's to write: /etc/fstab has one writer (roles/host_baseline,
host_baseline_mount_options), which also remounts, so a filesystem mounted without them
is refused, saying which to add. Accounts with no entry in limits (root, system
accounts) are never limited.

The project functions (require_project_quota, project_set, project_limit, project_used)
are also a library for tools that make projects at run time (clustertools.scratch_alloc).
Python 3.9 or newer, standard library only.
"""

import csv
import io
import json
import os
import re
import sys
import time
from pathlib import Path

from . import proc
from .cli import run_main
from .files import write_atomic
from .prom import key, render

MOUNTS = "/proc/self/mounts"
JOURNALED = ["usrjquota=aquota.user", "jqfmt=vfsv1"]
FEATURE = ["usrquota"]
QUOTA_FILE = "aquota.user"
# The mount options that turn project quotas on, per filesystem type.
PROJECT_OPTIONS = {"ext4": ("prjquota",), "xfs": ("prjquota", "pquota", "pqnoenforce")}
_UNITS = {"": 1, "K": 1, "M": 1024, "G": 1024**2, "T": 1024**3}
_INODE_UNITS = {"": 1, "k": 10**3, "m": 10**6, "g": 10**9, "t": 10**12}
TIMEOUT = 600  # quotacheck and chattr -R read a whole filesystem or tree


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


def levels(name, lim):
    """(block_warn, block_hard, inode_warn, inode_hard) of LIM in KiB and inodes."""
    values = (
        kib(lim.get("block_warn", 0)),
        kib(lim.get("block_hard", 0)),
        inodes(lim.get("inode_warn", 0)),
        inodes(lim.get("inode_hard", 0)),
    )
    if (values[0] and values[1] and values[0] > values[1]) or (
        values[2] and values[3] and values[2] > values[3]
    ):
        raise Error(f"{name}: a warn level above its hard limit")
    return values


def wanted(limits):
    """{user: (block_warn, block_hard, inode_warn, inode_hard)}."""
    return {user: levels(user, lim) for user, lim in (limits or {}).items()}


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

    def run(self, argv, check=True):
        return proc.run(argv, timeout=TIMEOUT, capture_output=True, text=True, check=check).stdout

    def xfs_quota(self, mount, cmd):
        return self.run(["xfs_quota", "-x", "-c", cmd, mount])

    def remove(self, path):
        os.unlink(path)


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
    if fstype != "ext4":
        raise Error(f"{mount} is {fstype}; user quotas are set on ext4 only")
    feature = has_feature(host.run(["tune2fs", "-l", device]))
    options = quota_options(feature)
    if not all(o in live for o in options):
        _refuse_options(mount, ",".join(options))
    steps = []
    quota_file = os.path.join(mount, QUOTA_FILE)
    if not feature and not host.exists(quota_file):
        check = ["quotacheck", "-cum", mount]
        steps.append((" ".join(check), lambda: host.run(check)))
    # quotaon on a filesystem whose quotas are on already fails: asked only when off.
    if steps or not re.search(r"\bis on\b", host.run(["quotaon", "-pu", mount], check=False)):
        on = ["quotaon", "-u", mount]
        steps.append((" ".join(on), lambda: host.run(on)))
    return steps


def limit_steps(host, mount, cfg, fresh=False):
    """[(description, action)] that set every user's hard limits on MOUNT (soft 0)."""
    want = wanted(cfg.get("limits"))
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


# Project quotas: a library (scratch_alloc) and the deploy-time projects.


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
        host.run(["chattr", "-R", "-p", str(pid), "+P", path])


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


def project_steps(host, cfg):
    """[(description, action)] that make each configured project's tree and limit."""
    steps = []
    on_checked = set()
    for path, proj in sorted((cfg.get("projects") or {}).items()):
        mount, pid = proj["filesystem"], int(proj["id"])
        warn, hard, _, _ = levels(path, proj)
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
    if re.search(r"\bis on\b", host.run(["quotaon", "-pu", mount], check=False)):
        have = report(host.run(["repquota", "-u", "-v", "-O", "csv", mount]))
        for user in sorted(set(cfg.get("limits") or {}) | set(cfg.get("release") or [])):
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
    want = wanted(cfg.get("limits"))
    for mount in cfg.get("filesystems") or []:
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
        warn = levels(path, proj)[0]
        labels = {"filesystem": mount, "project": path, "project_id": str(pid)}
        values[key("disk_quota_project_used_bytes", **labels)] = used * 1024
        for kind, b in (("warn", warn), ("hard", hard)):
            if b:
                values[key("disk_quota_project_limit_bytes", kind=kind, **labels)] = b * 1024
    values[key("disk_quota_last_success_timestamp_seconds")] = int(now)
    return render(values)


def steps_for(host, cfg, action):
    out = []
    if action == "remove":
        out += remove_project_steps(host, cfg)
        for mount in cfg.get("filesystems") or []:
            out += remove_steps(host, mount, cfg)
        return out
    for mount in cfg.get("filesystems") or []:
        fs = filesystem_steps(host, mount, cfg)
        out += fs
        # Quotas not on yet (they are turned on above): nothing can be read, and every
        # limit is set.
        out += limit_steps(host, mount, cfg, fresh=any(t.startswith("quotaon ") for t, _ in fs))
    return out + project_steps(host, cfg)


USAGE = "usage: disk-quota CONFIG.json apply|remove [--check] | metrics"


def main(argv=None):
    argv = sys.argv if argv is None else argv
    args = argv[1:]
    check = args[2:] == ["--check"]
    if not (len(args) == 2 or (check and len(args) == 3)) or args[1] not in ("apply", "remove", "metrics"):
        print(USAGE, file=sys.stderr)
        return 2
    if check and args[1] == "metrics":
        print(USAGE, file=sys.stderr)
        return 2

    def body():
        cfg = json.loads(sys.stdin.read() if args[0] == "-" else Path(args[0]).read_text())
        host = Host()
        try:
            if args[1] == "metrics":
                if cfg.get("textfile"):
                    write_atomic(cfg["textfile"], metrics(host, cfg, time.time()))
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
