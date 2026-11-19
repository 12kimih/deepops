"""slurm_backup: back up the Slurm controller's irreplaceable state, and prove it restores.

    slurm-backup backup CONFIG.json        daily: dump, archive, copy, prune, report
    slurm-backup restore-test CONFIG.json  monthly: load the newest dump into a scratch
                                           database and compare it with the live one
    slurm-backup publish CONFIG.json       at boot: republish the kept metrics
    slurm-backup check CONFIG.json         refuse a configuration the others cannot run

What is backed up: the accounting database (mariadb-dump, one consistent snapshot
with --single-transaction, compressed as it streams), the StateSaveLocation tree
(queued jobs, reservations) and, where a destination asks for it, the MUNGE key. A
SHA256SUMS file covers the set. Each destination keeps <daily> dated directories and
<weekly> more taken on the weekly day; a set is written under a hidden .partial name
and renamed when complete, so every dated directory is a whole set. A destination
owned by another user (an NFS export with root_squash) is written, listed, pruned and
read back as that user.

Every external step is bounded by step_timeout (seconds): a hung NFS destination fails
that destination, recorded as status 0, instead of blocking the run. Any failure,
including one before the first copy, still writes the metrics, so a stale or failed
backup raises an alert. The last success per destination, and of the restore test,
are node_exporter textfile metrics, kept in metrics_file (persistent; the textfile
directory is usually tmpfs) and copied into the textfile directory. Series of
destinations no longer configured are dropped. backup and restore-test hold one lock,
so they never overlap or interleave their metric updates.

Runs as root on the controller (mariadb root authenticates over the unix socket, so no
password is ever on a command line). Python 3.9 or newer, standard library only.
"""

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import sys
import tarfile
import tempfile
from pathlib import Path

from . import proc
from .files import write_atomic
from .prom import key, parse, render

DB_FILE = "slurm_acct_db.sql.gz"
STATE_FILE = "statesave.tar.gz"
MUNGE_FILE = "munge.key"
SUMS_FILE = "SHA256SUMS"
FILES = (DB_FILE, STATE_FILE, MUNGE_FILE)
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PARTIAL = re.compile(r"^\.\d{4}-\d{2}-\d{2}\.partial$")
PREFIX = "slurm_backup_"
STEP_TIMEOUT = 3600
KEEP = {"daily": 14, "weekly": 8}
# Failures one step can raise; each is reported, none ends the run without metrics.
# ValueError: a malformed answer (a count that is no number, a row of the wrong shape)
# fails the step and is recorded, never a traceback that skips the metrics.
FAILURES = (proc.CalledProcessError, proc.TimeoutExpired, OSError, tarfile.TarError, RuntimeError, ValueError)


# --- pure helpers (unit tested) ----------------------------------------------------


def problems_in(cfg):
    """What makes cfg unusable, as messages (empty when it is fine)."""
    out = []
    for name in ("database", "state_dir", "metrics_file", "textfile_dir", "textfile", "work_dir"):
        if not str(cfg.get(name) or ""):
            out.append(f"{name} is not set")
    if int(cfg.get("weekly_day", 7)) not in range(1, 8):
        out.append("weekly_day must be 1 (Monday) to 7 (Sunday)")
    if int(cfg.get("step_timeout", STEP_TIMEOUT)) <= 0:
        out.append("step_timeout must be positive")
    dests = cfg.get("destinations") or []
    if not dests:
        out.append("no destinations")
    paths = [str(d.get("path", "")) for d in dests]
    for dest, path in zip(dests, paths):
        if not path.startswith("/"):
            out.append(f"destination {path!r}: path must be absolute")
        for kind, default in KEEP.items():
            n = dest.get(kind, default)
            if not isinstance(n, int) or isinstance(n, bool) or n < 1:
                # 0 would delete the set just written; a destination you do not want
                # is removed from the list instead.
                out.append(f"destination {path}: {kind} must be a whole number of at least 1")
    if len(set(paths)) != len(paths):
        out.append("a destination path is listed twice")
    return out


def to_remove(names, keep):
    """Names to delete from a daily or weekly directory: dated sets beyond the newest
    `keep`, oldest first, then partial sets an interrupted run left behind."""
    dated = sorted(n for n in names if DATE.match(n))
    return dated[: max(len(dated) - keep, 0)] + sorted(n for n in names if PARTIAL.match(n))


def series(name, **labels):
    """A metric key for this program's series (the prefix added)."""
    return key(PREFIX + name, **labels)


def stale(metric, destinations):
    """Whether a kept series belongs to a destination no longer configured."""
    labels = dict(metric[1])
    return (
        metric[0].startswith(PREFIX) and "destination" in labels and labels["destination"] not in destinations
    )


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- side effects --------------------------------------------------------------------


def run_as(owner, argv, timeout, **kwargs):
    """argv as `owner` (runuser) unless owner is root, within `timeout` seconds."""
    if owner not in (None, "", "root"):
        argv = ["runuser", "-u", owner, "--", *argv]
    return proc.run(argv, timeout=timeout, **kwargs)


@contextlib.contextmanager
def locked(cfg):
    """Hold the one lock backup and restore-test share (waits for the other)."""
    work = Path(cfg["work_dir"])
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(work / "lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield work


def publish(cfg):
    """Copy the kept metrics into the textfile directory."""
    kept = Path(cfg["metrics_file"])
    if kept.exists():
        Path(cfg["textfile_dir"]).mkdir(parents=True, exist_ok=True)
        write_atomic(str(Path(cfg["textfile_dir"]) / cfg["textfile"]), kept.read_text())
    return 0


def write_metrics(cfg, values):
    """Merge values into the kept metrics (the other command's series stay, those of
    removed destinations go), then publish."""
    kept = Path(cfg["metrics_file"])
    paths = {d["path"] for d in cfg["destinations"]}
    merged = {
        k: v for k, v in (parse(kept.read_text()) if kept.exists() else {}).items() if not stale(k, paths)
    }
    merged.update(values)
    kept.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(str(kept), render(merged))
    publish(cfg)


def add_tree(tar, root):
    """Archive a directory a live daemon keeps rewriting: slurmctld replaces its state
    files by rename, so a file listed a moment ago may be gone; it is skipped, as GNU
    tar does, and its replacement is either archived or the next run's."""
    tar.add(root, arcname=root.name, recursive=False)
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames + filenames):
            path = Path(dirpath) / name
            with contextlib.suppress(FileNotFoundError):
                tar.add(path, arcname=str(root.name / path.relative_to(root)), recursive=False)


def build(cfg, staging, timeout):
    """Dump, archive and checksum into staging; return the file names."""
    dump = ["mariadb-dump", "--single-transaction", "--quick", "--routines", "--triggers", "--events"]
    with open(staging / DB_FILE, "wb") as out:
        try:
            # Compressed as it streams: no uncompressed copy of the database on disk.
            proc.run(
                ["bash", "-o", "pipefail", "-c", '"$@" | gzip -c', "bash", *dump, cfg["database"]],
                timeout=timeout,
                stdout=out,
            )
        except proc.CalledProcessError as e:
            raise RuntimeError(f"mariadb-dump failed ({e.returncode})") from None
    with tarfile.open(staging / STATE_FILE, "w:gz") as tar:
        add_tree(tar, Path(cfg["state_dir"]))
    files = [DB_FILE, STATE_FILE]
    if cfg.get("munge_key") and Path(cfg["munge_key"]).exists():
        (staging / MUNGE_FILE).write_bytes(Path(cfg["munge_key"]).read_bytes())
        files.append(MUNGE_FILE)
    (staging / SUMS_FILE).write_text("".join(f"{sha256(staging / f)}  {f}\n" for f in files))
    return files


def deliver(dest, staging, files, day, weekly, timeout):
    owner = dest.get("owner", "root")
    wanted = [f for f in files if f != MUNGE_FILE or dest.get("munge_key")]
    sums = "".join(
        line + "\n" for line in (staging / SUMS_FILE).read_text().splitlines() if line.split()[-1] in wanted
    )
    write = ["sh", "-c", 'umask 077 && cat > "$1"', "sh"]
    for kind in ["daily"] + (["weekly"] if weekly else []):
        base = Path(dest["path"]) / kind
        partial, final = base / f".{day}.partial", base / day
        run_as(owner, ["rm", "-rf", "--", str(partial)], timeout)
        run_as(owner, ["mkdir", "-p", "-m", "0700", str(base), str(partial)], timeout)
        for f in wanted:
            with open(staging / f, "rb") as src:
                run_as(owner, [*write, str(partial / f)], timeout, stdin=src)
        run_as(owner, [*write, str(partial / SUMS_FILE)], timeout, input=sums.encode())
        # Complete: it takes the dated name (a rerun the same day replaces that set).
        run_as(owner, ["rm", "-rf", "--", str(final)], timeout)
        run_as(owner, ["mv", "-T", "--", str(partial), str(final)], timeout)
        # Listed as the owner: on a root_squash export root cannot read the 0700 directory.
        names = run_as(
            owner, ["ls", "-1A", str(base)], timeout, capture_output=True, text=True
        ).stdout.split()
        for old in to_remove(names, dest.get(kind, KEEP[kind])):
            run_as(owner, ["rm", "-rf", "--", str(base / old)], timeout)
    return sum((staging / f).stat().st_size for f in wanted)


def backup(cfg):
    now = dt.datetime.now()
    stamp = int(now.timestamp())
    day = now.strftime("%Y-%m-%d")
    weekly = now.isoweekday() == int(cfg.get("weekly_day", 7))
    timeout = int(cfg.get("step_timeout", STEP_TIMEOUT))
    values = {series("last_run_timestamp_seconds"): stamp}
    failed = False
    with locked(cfg) as work, tempfile.TemporaryDirectory(prefix="backup-", dir=work) as tmp:
        staging = Path(tmp)
        try:
            files = build(cfg, staging, timeout)
        except FAILURES as exc:
            print(f"building the backup: {exc}", file=sys.stderr)
            files = None
        for dest in cfg["destinations"]:
            where = {"destination": dest["path"]}
            try:
                if files is None:
                    raise RuntimeError("nothing to copy: building the backup failed")
                size = deliver(dest, staging, files, day, weekly, timeout)
                values[series("last_success_timestamp_seconds", **where)] = stamp
                values[series("last_size_bytes", **where)] = size
                values[series("last_status", **where)] = 1
            except FAILURES as exc:
                if files is not None:
                    print(f"{dest['path']}: {exc}", file=sys.stderr)
                values[series("last_status", **where)] = 0
                failed = True
        write_metrics(cfg, values)
    return 1 if failed else 0


def mysql(sql, timeout, database=None):
    argv = ["mariadb", "--batch", "--skip-column-names"] + ([database] if database else []) + ["-e", sql]
    return proc.run(argv, timeout=timeout, capture_output=True, text=True).stdout


def counts(database, timeout):
    rows = mysql(
        "SELECT table_name, table_rows FROM information_schema.tables "
        f"WHERE table_schema = '{database}' AND table_type = 'BASE TABLE'",
        timeout,
    )
    out = {}
    for line in rows.splitlines():
        name, n = line.split("\t")
        out[name] = (
            int(mysql(f"SELECT COUNT(*) FROM `{name}`", timeout, database).strip() or 0) if n != "NULL" else 0
        )
    return out


def fetch_newest(dest, into, timeout):
    """Copy the first destination's newest daily set into `into`, read as its owner (a
    root_squash export included); return its path there and what is wrong with it."""
    owner = dest.get("owner", "root")
    daily = Path(dest["path"]) / "daily"
    names = run_as(owner, ["ls", "-1A", str(daily)], timeout, capture_output=True, text=True).stdout.split()
    dated = sorted(n for n in names if DATE.match(n))
    if not dated:
        raise RuntimeError(f"{daily}: no backup to test")
    newest = daily / dated[-1]
    sums = run_as(owner, ["cat", str(newest / SUMS_FILE)], timeout, capture_output=True, text=True).stdout
    listed, problems = {}, []
    for line in sums.splitlines():
        fields = line.split(None, 1)
        if len(fields) == 2:
            listed[fields[1]] = fields[0]
        elif fields:  # written where another account can write: report, never trust
            problems.append(f"{SUMS_FILE}: malformed line {line[:80]!r}")
    problems += [f"{name}: not in {SUMS_FILE}" for name in (DB_FILE, STATE_FILE) if name not in listed]
    # Only the names this program writes: the file is read from a destination another
    # account can write, and a name is a path root writes to here.
    problems += [f"{SUMS_FILE}: unexpected entry {name!r}" for name in listed if name not in FILES]
    if problems:
        return newest, problems
    for name, digest in listed.items():
        with open(into / name, "wb") as out:
            run_as(owner, ["cat", str(newest / name)], timeout, stdout=out)
        if sha256(into / name) != digest:
            problems.append(f"{name}: checksum mismatch")
    return newest, problems


def restore(cfg, files, timeout):
    """Load the dump into a scratch database and compare it with live; the problems."""
    problems = []
    with tarfile.open(files / STATE_FILE) as tar:
        if not tar.getnames():
            problems.append(f"{STATE_FILE}: empty")
    scratch = cfg.get("restore_database", "slurm_backup_restore_test")
    mysql(f"DROP DATABASE IF EXISTS `{scratch}`; CREATE DATABASE `{scratch}`", timeout)
    try:
        with open(files / DB_FILE, "rb") as dump:
            proc.run(
                ["bash", "-o", "pipefail", "-c", 'gzip -dc | "$@"', "bash", "mariadb", scratch],
                timeout=timeout,
                stdin=dump,
            )
        live, restored = counts(cfg["database"], timeout), counts(scratch, timeout)
        for table, n in live.items():
            if table not in restored:
                problems.append(f"table {table} missing from the backup")
            elif restored[table] > n:
                problems.append(f"table {table}: backup has more rows ({restored[table]}) than live ({n})")
            elif n and not restored[table]:
                problems.append(f"table {table}: empty in the backup, {n} rows live")
    finally:
        mysql(f"DROP DATABASE IF EXISTS `{scratch}`", timeout)
    return problems


def restore_test(cfg):
    """Fetch the first destination's newest set, verify it, restore it, compare."""
    timeout = int(cfg.get("step_timeout", STEP_TIMEOUT))
    newest, problems = cfg["destinations"][0]["path"], []
    with locked(cfg) as work, tempfile.TemporaryDirectory(prefix="restore-", dir=work) as tmp:
        try:
            newest, problems = fetch_newest(cfg["destinations"][0], Path(tmp), timeout)
            if not problems:
                problems = restore(cfg, Path(tmp), timeout)
        except FAILURES as exc:
            problems.append(str(exc))
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


def check(cfg):
    for p in problems_in(cfg):
        print(f"slurm-backup: {p}", file=sys.stderr)
    return 2 if problems_in(cfg) else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    commands = {"backup": backup, "restore-test": restore_test, "publish": publish, "check": check}
    if len(argv) != 2 or argv[0] not in commands:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    cfg = json.loads(Path(argv[1]).read_text())
    if argv[0] in ("backup", "restore-test") and check(cfg):
        return 2
    return commands[argv[0]](cfg)


if __name__ == "__main__":
    sys.exit(main())
