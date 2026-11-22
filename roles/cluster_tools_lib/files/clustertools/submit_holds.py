"""submit_holds: who may not submit Slurm jobs for now, and why, for job_submit.lua.

    submit-holds CONFIG.json     write the holds file and its metrics (as root, on a
                                 controller, every few minutes)

A person at a disk hard limit (clustertools.storage_report: the disk quota metrics, as
the daily disk report reads them) is held: their new jobs are refused, with the reason,
until they free space there. Running jobs go on. Before `disk_from` (Unix time, 0: at
once) no one is held: the grace a site gives everyone already at a limit when it turns
holds on.

The file is a line "generated <unix time>", then "<uid> <reason>" per person held; the
rendered job_submit.lua reads it at most once a minute and trusts it for half an hour,
so a run that fails (Prometheus away) leaves the last file, and a writer that stopped
holds no one once it is stale (an alert says so: SubmitHoldsStale). A login the host
cannot resolve is skipped, said on standard error.

CONFIG: prometheus_url, file (the holds), disk_from, textfile (node_exporter's).
Python 3.9 or newer, standard library only.
"""

import pwd
import sys
import time

from . import config, storage_report
from .cli import run_main
from .files import write_atomic
from .prom import key, render
from .prometheus import Error as PrometheusError
from .prometheus import Prometheus

CONFIG_KEYS = {"prometheus_url": str, "file": str, "disk_from": (int, float), "textfile": str}


def load_config(path):
    """The config, checked whole (clustertools.config)."""
    return config.require(path, CONFIG_KEYS)


def uid_of(login):
    try:
        return pwd.getpwnam(login).pw_uid
    except KeyError:
        return None


def disk_holds(data):
    """{login: reason} for everyone at a disk hard limit in DATA (storage_report.collect),
    the worst of their filesystems."""
    out = {}
    for row in storage_report.over_warn(data):
        place, amount, state, hard = storage_report.describe(row)
        if hard and row[2] not in out:
            out[row[2]] = (
                f"Your disk use on {place} is {amount}, {state}. New jobs are held until you"
                f" free space there (`quota -s` on {row[0]} shows it); running jobs go on."
            )
    return out


def holds(data, cfg, now, uid=uid_of):
    """{uid: reason} of everyone held NOW; nobody before disk_from."""
    if data is None or now < cfg["disk_from"]:
        return {}
    out = {}
    for login, why in sorted(disk_holds(data).items()):
        n = uid(login)
        if n is None:
            print(f"submit-holds: {login} has no account here; not held", file=sys.stderr)
            continue
        out[n] = why
    return out


def text(held, now):
    """The holds file: its stamp, then a line per uid (a reason is one line)."""
    lines = [f"generated {int(now)}"]
    lines += [f"{uid} {' '.join(why.split())}" for uid, why in sorted(held.items())]
    return "\n".join(lines) + "\n"


def metrics(held, cfg, now):
    return render(
        {
            key("submit_holds_people"): len(held),
            key("submit_holds_grace_until_timestamp_seconds"): int(cfg["disk_from"]),
            key("submit_holds_last_success_timestamp_seconds"): int(now),
        }
    )


def main(argv=None):
    args = (sys.argv if argv is None else argv)[1:]
    if len(args) != 1:
        print("usage: submit-holds CONFIG.json", file=sys.stderr)
        return 2

    def body():
        cfg = load_config(args[0])
        now = time.time()
        try:
            data = storage_report.collect(Prometheus(cfg["prometheus_url"]), now)
        except PrometheusError as e:  # the last file stays, trusted until it is stale
            print(f"submit-holds: {e}", file=sys.stderr)
            return 1
        held = holds(data, cfg, now)
        write_atomic(cfg["file"], text(held, now), mode=0o644)
        write_atomic(cfg["textfile"], metrics(held, cfg, now), mode=0o644)
        return 0

    return run_main("submit-holds", body)


if __name__ == "__main__":
    sys.exit(main())
