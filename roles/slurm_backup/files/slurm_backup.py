#!/usr/bin/env python3
"""Back up the Slurm controller's irreplaceable state, and prove a backup restores.

    slurm-backup backup CONFIG.json        daily: dump, archive, copy, prune, report
    slurm-backup restore-test CONFIG.json  monthly: load the newest dump into a scratch
                                           database and compare it with the live one
    slurm-backup publish CONFIG.json       at boot: republish the kept metrics

What is backed up: the accounting database (mariadb-dump, one consistent snapshot
with --single-transaction), the StateSaveLocation tree (queued jobs, reservations)
and, where a destination asks for it, the MUNGE key. A SHA256SUMS file covers the
set. Each destination keeps <daily> dated directories and <weekly> more taken on the
weekly day; a destination owned by another user (an NFS export with root_squash) is
written as that user. The last success per destination, and of the restore test, is
written as node_exporter textfile metrics so a stale backup raises an alert. They are
kept in metrics_file (persistent; the textfile directory is usually tmpfs) and copied
into the textfile directory, so they survive a reboot: an absent series never alerts.

Standard library only; runs as root on the controller (mariadb root authenticates
over the unix socket, so no password is ever on a command line).
"""

import datetime as dt
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

DB_FILE = "slurm_acct_db.sql.gz"
STATE_FILE = "statesave.tar.gz"
MUNGE_FILE = "munge.key"
SUMS_FILE = "SHA256SUMS"
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --- pure helpers (unit tested) ----------------------------------------------------


def to_remove(names, keep):
    """Dated directory names beyond the newest `keep`, oldest first."""
    dated = sorted(n for n in names if DATE.match(n))
    return dated[: max(len(dated) - keep, 0)]


def series(name, **labels):
    """A metric key: (name, sorted label pairs)."""
    return (name, tuple(sorted(labels.items())))


def metrics(prefix, values):
    """Prometheus textfile lines for {series(...): value}, sorted."""
    lines = []
    for (name, labels), value in sorted(values.items()):
        label = ",".join(f'{k}="{v}"' for k, v in labels)
        lines.append(f"{prefix}_{name}{{{label}}} {value}")
    return "\n".join(lines) + "\n"


def parse_metrics(prefix, text):
    """The inverse of metrics()."""
    values = {}
    for line in text.splitlines():
        m = re.match(rf"^{prefix}_(\w+)\{{(.*)\}} (\S+)$", line)
        if m:
            values[series(m.group(1), **dict(re.findall(r'(\w+)="([^"]*)"', m.group(2))))] = m.group(3)
    return values


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- side effects --------------------------------------------------------------------


def run_as(owner, argv, **kwargs):
    """argv as `owner` (runuser) unless owner is root."""
    if owner not in (None, "", "root"):
        argv = ["runuser", "-u", owner, "--", *argv]
    return subprocess.run(argv, check=True, **kwargs)


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    tmp.chmod(0o644)
    os.replace(tmp, path)


def publish(cfg):
    """Copy the kept metrics into the textfile directory."""
    kept = Path(cfg["metrics_file"])
    if kept.exists():
        atomic_write(Path(cfg["textfile_dir"]) / cfg["textfile"], kept.read_text())
    return 0


def write_metrics(cfg, values):
    """Merge values into the kept metrics (the other command's series stay), then publish."""
    kept = Path(cfg["metrics_file"])
    merged = parse_metrics("slurm_backup", kept.read_text()) if kept.exists() else {}
    merged.update(values)
    atomic_write(kept, metrics("slurm_backup", merged))
    publish(cfg)


def build(cfg, staging):
    """Dump, archive and checksum into staging; return the file names."""
    dump = staging / DB_FILE
    argv = [
        "mariadb-dump",
        "--single-transaction",
        "--quick",
        "--routines",
        "--triggers",
        "--events",
        cfg["database"],
    ]
    with gzip.open(dump, "wb") as out, subprocess.Popen(argv, stdout=subprocess.PIPE) as proc:
        shutil.copyfileobj(proc.stdout, out)
    if proc.returncode != 0:
        raise RuntimeError(f"mariadb-dump failed ({proc.returncode})")
    state = Path(cfg["state_dir"])
    with tarfile.open(staging / STATE_FILE, "w:gz") as tar:
        tar.add(state, arcname=state.name)
    files = [DB_FILE, STATE_FILE]
    if cfg.get("munge_key") and Path(cfg["munge_key"]).exists():
        shutil.copy2(cfg["munge_key"], staging / MUNGE_FILE)
        files.append(MUNGE_FILE)
    (staging / SUMS_FILE).write_text("".join(f"{sha256(staging / f)}  {f}\n" for f in files))
    return files


def deliver(dest, staging, files, day, weekly):
    owner = dest.get("owner", "root")
    wanted = [f for f in files if f != MUNGE_FILE or dest.get("munge_key")]
    sums = "".join(
        line + "\n" for line in (staging / SUMS_FILE).read_text().splitlines() if line.split()[-1] in wanted
    )
    for kind in ["daily"] + (["weekly"] if weekly else []):
        target = Path(dest["path"]) / kind / day
        run_as(owner, ["mkdir", "-p", "-m", "0700", str(target)])
        for f in wanted:
            with open(staging / f, "rb") as src:
                run_as(owner, ["sh", "-c", 'umask 077 && cat > "$1"', "sh", str(target / f)], stdin=src)
        run_as(
            owner, ["sh", "-c", 'umask 077 && cat > "$1"', "sh", str(target / SUMS_FILE)], input=sums.encode()
        )
        base = Path(dest["path"]) / kind
        keep = dest.get(kind, 14 if kind == "daily" else 8)
        for old in to_remove(os.listdir(base), keep):
            run_as(owner, ["rm", "-rf", "--", str(base / old)])
    return sum((staging / f).stat().st_size for f in wanted)


def backup(cfg):
    now = dt.datetime.now()
    day = now.strftime("%Y-%m-%d")
    weekly = now.isoweekday() == int(cfg.get("weekly_day", 7))
    values = {series("last_run_timestamp_seconds"): int(now.timestamp())}
    failed = False
    with tempfile.TemporaryDirectory(prefix="slurm-backup-") as tmp:  # 0700, root only
        staging = Path(tmp)
        files = build(cfg, staging)
        for dest in cfg["destinations"]:
            where = {"destination": dest["path"]}
            try:
                size = deliver(dest, staging, files, day, weekly)
                values[series("last_success_timestamp_seconds", **where)] = int(now.timestamp())
                values[series("last_size_bytes", **where)] = size
                values[series("last_status", **where)] = 1
            except (subprocess.CalledProcessError, OSError) as exc:
                print(f"{dest['path']}: {exc}", file=sys.stderr)
                values[series("last_status", **where)] = 0
                failed = True
    write_metrics(cfg, values)
    return 1 if failed else 0


def mysql(sql, database=None):
    argv = ["mariadb", "--batch", "--skip-column-names"] + ([database] if database else []) + ["-e", sql]
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout


def counts(database):
    rows = mysql(
        "SELECT table_name, table_rows FROM information_schema.tables "
        f"WHERE table_schema = '{database}' AND table_type = 'BASE TABLE'"
    )
    out = {}
    for line in rows.splitlines():
        name, n = line.split("\t")
        out[name] = int(mysql(f"SELECT COUNT(*) FROM `{name}`", database).strip() or 0) if n != "NULL" else 0
    return out


def restore_test(cfg):
    """Load the newest local backup into a scratch database and compare it with live."""
    dest = next(d for d in cfg["destinations"] if d.get("owner", "root") == "root")
    daily = Path(dest["path"]) / "daily"
    newest = daily / max(n for n in os.listdir(daily) if DATE.match(n))
    problems = []
    for line in (newest / SUMS_FILE).read_text().splitlines():
        digest, name = line.split()
        if sha256(newest / name) != digest:
            problems.append(f"{name}: checksum mismatch")
    with tarfile.open(newest / STATE_FILE) as tar:
        if not tar.getnames():
            problems.append(f"{STATE_FILE}: empty")
    scratch = cfg.get("restore_database", "slurm_backup_restore_test")
    mysql(f"DROP DATABASE IF EXISTS `{scratch}`; CREATE DATABASE `{scratch}`")
    try:
        with gzip.open(newest / DB_FILE, "rb") as dump:
            subprocess.run(["mariadb", scratch], stdin=dump, check=True)
        live, restored = counts(cfg["database"]), counts(scratch)
        for table, n in live.items():
            if table not in restored:
                problems.append(f"table {table} missing from the backup")
            elif restored[table] > n:
                problems.append(f"table {table}: backup has more rows ({restored[table]}) than live ({n})")
            elif n and not restored[table]:
                problems.append(f"table {table}: empty in the backup, {n} rows live")
    finally:
        mysql(f"DROP DATABASE IF EXISTS `{scratch}`")
    now = int(dt.datetime.now().timestamp())
    values = {
        series("restore_test_last_run_timestamp_seconds"): now,
        series("restore_test_status"): 0 if problems else 1,
    }
    if not problems:
        values[series("restore_test_last_success_timestamp_seconds")] = now
    write_metrics(cfg, values)
    for p in problems:
        print(f"{newest}: {p}", file=sys.stderr)
    print(f"restore test of {newest}: {'FAILED' if problems else 'ok'}")
    return 1 if problems else 0


def main(argv):
    commands = {"backup": backup, "restore-test": restore_test, "publish": publish}
    if len(argv) != 2 or argv[0] not in commands:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    return commands[argv[0]](json.loads(Path(argv[1]).read_text()))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
