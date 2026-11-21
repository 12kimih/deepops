"""disk_quota: per-user disk quotas on local ext4 filesystems, and their usage as metrics.

    disk-quota CONFIG.json apply [--check]   set them up; --check prints the steps only
    disk-quota CONFIG.json remove [--check]  undo it: limits, quotas, the quota file
    disk-quota CONFIG.json metrics           every user's usage and limits (textfile)

Each step is printed as it is taken (or would be, with --check), one line each. CONFIG
"-" is read from standard input (a preview of a configuration not written yet).
CONFIG names the filesystems (mount points), each user's limits, the users whose
limits go (in the roster but limited no longer), the grace period and the textfile:

    {"filesystems": ["/"],
     "limits": {"alice": {"block_soft": "10G", "block_hard": "20G",
                          "inode_soft": 200000, "inode_hard": 500000}},
     "release": ["bob"], "grace_days": 7,
     "textfile": "/run/prometheus/disk-quota.prom"}

A filesystem with ext4's quota feature (tune2fs -O quota) needs usrquota in its mount
options; one without it keeps journaled quota files (usrjquota=aquota.user,jqfmt=vfsv1,
created by quotacheck), which can be turned on while it is mounted. The mount options
are not this tool's to write: /etc/fstab has one writer (roles/host_baseline,
host_baseline_mount_options), which also remounts, so a filesystem mounted without them
is refused, saying which to add; systemd's fstab generator turns quotas on at every
boot. Accounts with no entry in limits (root, system accounts) are never limited.
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
_UNITS = {"": 1, "K": 1, "M": 1024, "G": 1024**2, "T": 1024**3}
_INODE_UNITS = {"": 1, "k": 10**3, "m": 10**6, "g": 10**9, "t": 10**12}
TIMEOUT = 600  # quotacheck reads every inode of the filesystem


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


def wanted(limits):
    """{user: (block_soft, block_hard, inode_soft, inode_hard)} in KiB and inodes."""
    out = {}
    for user, lim in (limits or {}).items():
        values = (
            kib(lim.get("block_soft", 0)),
            kib(lim.get("block_hard", 0)),
            inodes(lim.get("inode_soft", 0)),
            inodes(lim.get("inode_hard", 0)),
        )
        if (values[0] and values[1] and values[0] > values[1]) or (
            values[2] and values[3] and values[2] > values[3]
        ):
            raise Error(f"{user}: a soft limit above its hard limit")
        out[user] = values
    return out


def _unescape(field):
    """A mount table or fstab field: spaces and tabs are written as octal escapes."""
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
    """{user: (block_used, block_soft, block_hard, inode_used, inode_soft, inode_hard)}
    from repquota -u -v -O csv (blocks in KiB)."""
    out = {}
    rows = csv.DictReader(io.StringIO(text))
    for row in rows:
        name = (row.get("User") or row.get("Name") or "").lstrip("#")
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


def grace_shown(text):
    """The (block, inode) grace periods repquota's default report prints in its header."""
    m = re.search(r"Block grace time: (\S+); Inode grace time: (\S+)", text)
    return (m.group(1), m.group(2)) if m else None


class Host:
    """The commands and files the tool reads and changes: replaced in the tests."""

    def read(self, path):
        return Path(path).read_text()

    def exists(self, path):
        return os.path.exists(path)

    def run(self, argv, check=True):
        return proc.run(argv, timeout=TIMEOUT, capture_output=True, text=True, check=check).stdout

    def remove(self, path):
        os.unlink(path)


def _limits_of(entry):
    return entry[1], entry[2], entry[4], entry[5]


def quota_options(feature):
    """The mount options per-user quotas need: usrquota with ext4's quota feature,
    journaled quota files without it."""
    return FEATURE if feature else JOURNALED


def filesystem_steps(host, mount, cfg):
    """[(description, action)] that turn MOUNT's per-user quotas on, in order."""
    table = mounts(host.read(MOUNTS))
    if mount not in table:
        raise Error(f"{mount} is not mounted")
    device, fstype, live = table[mount]
    if fstype != "ext4":
        raise Error(f"{mount} is {fstype}; this tool sets quotas on ext4 only")
    feature = has_feature(host.run(["tune2fs", "-l", device]))
    options = quota_options(feature)
    if not all(o in live for o in options):
        raise Error(
            f"{mount} is mounted without {','.join(options)}: add them to its entry in"
            f" host_baseline_mount_options and run playbooks/generic/host-baseline.yml"
            f" (it sets /etc/fstab and remounts), then run this again"
        )
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
    """[(description, action)] that set every user's limits on MOUNT and the grace."""
    want = wanted(cfg.get("limits"))
    have = {} if fresh else report(host.run(["repquota", "-u", "-v", "-O", "csv", mount]))
    steps = []
    days = int(cfg.get("grace_days", 7))
    shown = None if fresh else grace_shown(host.run(["repquota", "-u", mount]))
    if shown != (f"{days}days", f"{days}days"):
        grace = ["setquota", "-t", "-u", str(days * 86400), str(days * 86400), mount]
        steps.append((" ".join(grace), lambda g=grace: host.run(g)))
    for user, values in sorted(want.items()):
        if user not in have or _limits_of(have[user]) != values:
            argv = ["setquota", "-u", user] + [str(v) for v in values] + [mount]
            steps.append((" ".join(argv), lambda a=argv: host.run(a)))
    for user in sorted(set(cfg.get("release") or []) - set(want)):
        if user in have and any(_limits_of(have[user])):
            argv = ["setquota", "-u", user, "0", "0", "0", "0", mount]
            steps.append((" ".join(argv), lambda a=argv: host.run(a)))
    return steps


def remove_steps(host, mount, cfg):
    """[(description, action)] that take MOUNT's quotas away: the limits, the quotas
    and the quota file. The mount options are host_baseline_mount_options' to drop:
    with them left, the next boot turns quotas on again."""
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


def metrics(host, cfg, now):
    """Every user's usage and limits on each filesystem, as textfile metrics."""
    values = {}
    for mount in cfg.get("filesystems") or []:
        for user, (bu, bs, bh, iu, isoft, ih) in report(
            host.run(["repquota", "-u", "-v", "-O", "csv", mount])
        ).items():
            labels = {"filesystem": mount, "user": user}
            values[key("disk_quota_used_bytes", **labels)] = bu * 1024
            values[key("disk_quota_used_inodes", **labels)] = iu
            for kind, b, i in (("soft", bs, isoft), ("hard", bh, ih)):
                if b:
                    values[key("disk_quota_limit_bytes", kind=kind, **labels)] = b * 1024
                if i:
                    values[key("disk_quota_limit_inodes", kind=kind, **labels)] = i
    values[key("disk_quota_last_success_timestamp_seconds")] = int(now)
    return render(values)


def steps_for(host, cfg, action):
    out = []
    for mount in cfg.get("filesystems") or []:
        if action == "remove":
            out += remove_steps(host, mount, cfg)
        else:
            fs = filesystem_steps(host, mount, cfg)
            out += fs
            # Quotas not on yet (they are turned on above): nothing can be read, and
            # every limit is set.
            out += limit_steps(host, mount, cfg, fresh=any(t.startswith("quotaon ") for t, _ in fs))
    return out


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
