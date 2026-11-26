"""usage io: how hard you load the shared storage: your jobs' and logins' I/O lately, each
node of yours and its share of what the disk behind each NFS export serves, and whether
the I/O notice is due (cluster_tools.rules: io_report's own judgement of the hot nodes).

All from Prometheus (nothing is measured here):
  I/O     roles/io_usage's counters: each job's reads and writes through system calls
          (page cache and pipes included: a ranking of your jobs, not their disk or NFS
          bytes) and each login's block I/O on the node's own disks, outside jobs
  nodes   each node running a job of yours: what it reads from and writes to each
          measured export over the wire (host_mount:nfs_*_bytes:rate5m), against what
          the disk behind it serves (roles/io_fairness' models): the notice's measure
The Grafana dashboard "Storage / per person" (scripts/dashboards/storage_person.py)
draws the same I/O queries (io below) over time. Python 3.9 or newer, standard library only.
"""

import sys

from . import rules, slurm
from .usage import JSON_NOTE
from .cli import UNKNOWN, Command, Opt, opt_json, opt_user, run_main
from .disk_report import host_of
from .io_report import DIRECTIONS, quoted
from .render import Grid, size

# (who, direction): (counter, the labels a row keeps)
IO = {
    ("job", "read"): ("io_usage_job_read_bytes_total", "host, job_id"),
    ("job", "written"): ("io_usage_job_written_bytes_total", "host, job_id"),
    ("login", "read"): ("io_usage_login_read_bytes_total", "host, device"),
    ("login", "written"): ("io_usage_login_written_bytes_total", "host, device"),
}


def io(who, direction, selector, window, function="increase"):
    """The bytes (FUNCTION increase) or bytes a second (rate) of the person's jobs or
    logins over WINDOW, one series per job or per disk of a login."""
    counter, by = IO[(who, direction)]
    return f"sum by ({by}) ({function}({counter}{{{selector}}}[{window}]))"


COMMAND = Command(
    "usage io",
    "your jobs' and logins' I/O, your nodes' share of the shared storage, whether a notice is due",
    [
        opt_user("someone else's figures instead of yours", metavar="NAME"),
        Opt(
            "--hours",
            metavar="N",
            type=int,
            default=24,
            brief=True,
            help="the I/O window, in hours (default 24)",
        ),
        Opt("--top", metavar="N", type=int, default=10, help="rows per table (default 10)"),
        opt_json("one object, schema 1 (below): jobs, logins, nodes"),
    ],
    usage="usage io [OPTIONS]",
    machine="--json",
    notes=[__doc__.split("\n\n", 1)[1].rsplit("\n", 1)[0], JSON_NOTE],
    summary_ko="내 잡과 로그인의 I/O, 내 노드가 공유 스토리지에 거는 부하, 알림 여부",
)


def _series(api, expr, labels):
    return {
        (host_of(r["metric"]),) + tuple(r["metric"].get(n, "") for n in labels): float(r["value"][1])
        for r in api.query(expr)
    }


def collect(api, user, hours, top, mounts=None, hosts=()):
    """The person's I/O rows (jobs, logins) and their nodes' load on each measured export."""
    selector = f"user={quoted(user)}"
    out = {"hours": hours, "jobs": [], "logins": [], "nodes": []}
    for who, label in (("job", "job_id"), ("login", "device")):
        moved = {}
        for direction in ("read", "written"):
            for k, v in _series(api, io(who, direction, selector, f"{hours}h"), (label,)).items():
                moved.setdefault(k, {"read": 0.0, "written": 0.0})[direction] = v
        listed = [
            {"host": h, label: name, **d} for (h, name), d in moved.items() if d["read"] or d["written"]
        ]
        listed.sort(key=lambda r: (-(r["read"] + r["written"]), r["host"], r[label]))
        out[f"{who}s"] = listed[:top]
    if mounts and hosts:
        pick = "|".join(sorted(hosts))
        for direction, (recorded, cap_key, *_) in DIRECTIONS.items():
            for (host, mount), rate in _series(api, f'{recorded}{{host=~"{pick}"}}', ("mountpoint",)).items():
                if mount in mounts:
                    cap = mounts[mount][cap_key]
                    out["nodes"].append({"host": host, "mount": mount, "direction": direction, "rate": rate,
                                         "share": rate / cap if cap else None})  # fmt: skip
        out["nodes"].sort(key=lambda n: (-(n["share"] or 0), n["host"]))
    return out


def render(data, theme, share):
    out = []
    for key, label, heading, title in (("jobs", "job_id", "JOB", "I/O of jobs"), ("logins", "device", "DISK", "I/O of logins")):  # fmt: skip
        rows = data[key]
        grid = Grid(theme, [heading, "HOST", "READ", "WRITTEN"], align=["l", "l", "r", "r"])
        for r in rows:
            grid.row(
                [
                    (r[label], "text"),
                    (r["host"], "muted"),
                    (size(r["read"]), "text"),
                    (size(r["written"]), "text"),
                ]
            )
        tally = [(f"last {data['hours']}h", "sub")] + ([] if rows else [("none", "muted")])
        out.append(grid.render(title, tally))
    if data["nodes"]:
        grid = Grid(theme, ["NODE", "EXPORT", "WAY", "NOW", "OF THE DISK"], align=["l", "l", "l", "r", "r"])
        for n in data["nodes"]:
            hot = n["share"] is not None and n["share"] > share
            grid.row([(n["host"], "text"), (n["mount"], "muted"), (n["direction"], "muted"), (f"{size(n['rate'])}/s", "text"),
                      (f"{n['share']:.0%}" if n["share"] is not None else "-", "warn" if hot else "text")])  # fmt: skip
        out.append(
            grid.render(
                "your nodes on the shared storage", [(f"a notice past {share:.0%} for its window", "sub")]
            )
        )
    return "\n".join(out) + "\n"


def main(argv=None):
    from .usage import context, emit, table

    args, theme = COMMAND.parse(argv)

    def body():
        if args.hours < 1 or args.top < 1:
            print("usage io: --hours and --top take a whole number from 1", file=sys.stderr)
            return 2
        user, _, cfg, api = context(args.user)
        if api is None:
            print("usage io: no prometheus_url", file=sys.stderr)
            return UNKNOWN
        part = cfg.get("io") or {}
        running = [
            j
            for j in slurm.load("jobs")["jobs"]
            if j.get("user_name") == user and "RUNNING" in (j.get("job_state") or [])
        ]
        hosts = {h for j in running for h in slurm.hostnames(j.get("nodes") or "")}
        data = collect(api, user, args.hours, args.top, part.get("mounts"), hosts)
        row = rules.io(user, cfg, api)
        text = table([row], theme, f"shared storage load of {user}") + render(
            data, theme, float(part.get("share") or 1)
        )
        return emit("usage io", user, [row], args, theme, text, **data)

    return run_main("usage io", body, unknown=UNKNOWN)


if __name__ == "__main__":
    sys.exit(main())
