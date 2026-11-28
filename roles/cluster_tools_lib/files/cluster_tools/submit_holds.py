"""submit_holds: who may not submit Slurm jobs for now, and why, for job_submit.lua.

    submit-holds CONFIG.json     write the holds file and its metrics (as root, on a
                                 controller, every few minutes)

Held, their new jobs refused with the reason while running jobs go on:
- a person past their membership's end (`ends`: login -> {ends, login_expires}, the
  roster's people_leaving): from the day after `ends`, local time, with no deploy; their
  login lasts until login_expires to move their files. Needs no Prometheus.
- with `disk`, a person at a disk hard limit the kernel enforces (cluster_tools.disk_report:
  the disk quota metrics, as the daily disk report reads them), until they free space
  there. A limit announced and not enforced yet (its "from" day, roles/disk_quota) holds
  no one: the grace a site gives everyone already past it.

The file is a line "generated <unix time>", then "<uid> <reason>" per person held; the
rendered job_submit.lua reads it at most once a minute and trusts it for `max_age`
seconds (submit_holds_max_age), so a writer that stopped holds no one once it is stale;
the metrics carry `max_age` for the alert that says so first (SubmitHoldsStale). A run that cannot read Prometheus still writes the ends holds and
keeps the last file's disk holds, exits 3 and leaves the metrics (so the alert fires).
A login the host cannot resolve is skipped, said on standard error.

CONFIG: prometheus_url, file (the holds), disk, ends, textfile (node_exporter's), max_age
(optional: a config written before it gives no such metric); disk_from, retired (each
limit has its own day), is accepted and read by nothing until the role rewrites it.
Python 3.9 or newer, standard library only.
"""

import pwd
import sys
import time

from . import config, disk_report
from .cli import run_main
from .exits import CANNOT_RUN, OK, UNKNOWN
from .files import write_atomic
from .prom import key, render
from .prometheus import Error as PrometheusError
from .prometheus import Prometheus

CONFIG_KEYS = {
    "prometheus_url": str, "file": str, "disk": bool, "ends": dict, "textfile": str,
}  # fmt: skip
CONFIG_OPTIONAL = {"max_age": int, "disk_from": (int, float)}
# How an ends hold's reason begins: what a run that could not read the disk metrics
# tells apart from the disk holds it keeps.
ENDED = "Your membership ended on"
# How a disk hold's reason begins (the user docs quote both, scripts/docs/site_facts.py).
DISK_HELD = "Your disk use on"


def load_config(path):
    """The config, checked whole (cluster_tools.config)."""
    return config.require(path, CONFIG_KEYS, CONFIG_OPTIONAL)


def uid_of(login):
    try:
        return pwd.getpwnam(login).pw_uid
    except KeyError:
        return None


def disk_holds(data):
    """{login: reason} for everyone at a disk hard limit in DATA (disk_report.collect),
    the worst of their filesystems."""
    out = {}
    for row in disk_report.over_warn(data):
        # In English, as every message sbatch shows (job_submit.lua); the figures are the
        # notices' own (disk_report.amounts).
        place, what, used, level, kind, _ = disk_report.amounts(row)
        figure = disk_report.size if what == "space" else (lambda n: f"{n:,.0f} files")
        if kind == "hard" and disk_report.enforced(row[3]) and row[2] not in out:
            out[row[2]] = (
                f"{DISK_HELD} {place} is {figure(used)}, at the hard limit {figure(level)}: writes"
                f" there fail now. New jobs are held until you free space there"
                f" (`{disk_report.USAGE}` shows it); running jobs go on."
            )
    return out


def ends_holds(ends, now):
    """{login: reason} for everyone past their end NOW (the day after `ends`, local)."""
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    return {
        login: (
            f"{ENDED} {e['ends']}. New jobs are refused; your login lasts until"
            f" {e['login_expires']} to move your files away."
        )
        for login, e in sorted(ends.items())
        if str(e.get("ends", "")) and today > str(e["ends"])
    }


def holds(data, cfg, now, uid=None):
    """{uid: reason} of everyone held NOW: past their end, or (with disk, when DATA was
    read) at an enforced disk hard limit. An end goes first: it is the lasting one."""
    uid = uid or uid_of
    reasons = {}
    if cfg["disk"] and data is not None:
        reasons.update(disk_holds(data))
    reasons.update(ends_holds(cfg["ends"], now))
    out = {}
    for login, why in sorted(reasons.items()):
        n = uid(login)
        if n is None:
            print(f"submit-holds: {login} has no account here; not held", file=sys.stderr)
            continue
        out[n] = why
    return out


def kept_disk_holds(path):
    """{uid: reason} of the disk holds in the file at PATH (none when it is missing)."""
    try:
        with open(path) as f:
            lines = f.read().splitlines()[1:]
    except OSError:
        return {}
    out = {}
    for line in lines:
        uid, _, why = line.partition(" ")
        if uid.isdigit() and not why.startswith(ENDED):
            out[int(uid)] = why
    return out


def text(held, now):
    """The holds file: its stamp, then a line per uid (a reason is one line)."""
    lines = [f"generated {int(now)}"]
    lines += [f"{uid} {' '.join(why.split())}" for uid, why in sorted(held.items())]
    return "\n".join(lines) + "\n"


def metrics(held, cfg, now):
    values = {
        key("submit_holds_people"): len(held),
        key("submit_holds_last_success_timestamp_seconds"): int(now),
    }
    if "max_age" in cfg:
        values[key("submit_holds_max_age_seconds")] = cfg["max_age"]
    return render(values)


def main(argv=None):
    args = (sys.argv if argv is None else argv)[1:]
    if len(args) != 1:
        print("usage: submit-holds CONFIG.json", file=sys.stderr)
        return CANNOT_RUN

    def body():
        cfg = load_config(args[0])
        now = time.time()
        data, kept, failed = None, {}, False
        if cfg["disk"]:
            try:
                data = disk_report.collect(Prometheus(cfg["prometheus_url"]), now)
            except PrometheusError as e:  # the ends holds still go out; the disk ones stay
                print(f"submit-holds: {e}", file=sys.stderr)
                kept, failed = kept_disk_holds(cfg["file"]), True
        held = {**kept, **holds(data, cfg, now)}
        write_atomic(cfg["file"], text(held, now), mode=0o644)
        if failed:
            return UNKNOWN
        write_atomic(cfg["textfile"], metrics(held, cfg, now), mode=0o644)
        return OK

    return run_main("submit-holds", body)


if __name__ == "__main__":
    sys.exit(main())
