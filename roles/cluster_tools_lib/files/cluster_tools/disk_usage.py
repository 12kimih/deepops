"""disk-usage: your space on each filesystem against its levels, your folders worth
cleaning, and how much your jobs and logins read and wrote lately.

All from Prometheus (prometheus_url in the site file), the series the cluster already
keeps; nothing is measured here:
  space    roles/disk_quota's metrics: the bytes and files each person holds on each
           counted filesystem, the warn level (a notice) and the hard limit (the kernel
           refuses the write that would pass it)
  folders  the folders worth cleaning (cluster_tools.cleanup: unused past the stale
           period), the largest and longest unused first
  I/O      roles/io_usage's counters: each job's reads and writes through system calls
           (page cache and pipes included: a ranking, not disk or NFS bytes) and each
           login's block I/O on the node's own disks, outside jobs
The Grafana dashboard "Storage / per person" (scripts/dashboards/storage_person.py)
draws the same queries (space, folders, io below) over time, for the person picked.
Python 3.9 or newer, standard library only.
"""

import json
import sys
import time

from .cli import Command, Opt, me, opt_json, opt_user, run_main
from .disk_report import host_of, where
from .io_report import quoted
from .prometheus import Error as PrometheusError
from .prometheus import Prometheus
from .render import Grid, size, width
from .site import PATH as SITE_PATH
from .site import info as site_info

# The person's series. SELECTOR is a PromQL label matcher on the person: user="kim"
# here, user=~"$user" on the dashboard, so both read one definition.
SPACE = (
    # (figure, metric, kind label or None)
    ("used", "disk_quota_used_bytes", None),
    ("warn", "disk_quota_limit_bytes", "warn"),
    ("hard", "disk_quota_limit_bytes", "hard"),
    ("files", "disk_quota_used_inodes", None),
    ("file_warn", "disk_quota_limit_inodes", "warn"),
    ("file_hard", "disk_quota_limit_inodes", "hard"),
)
FOLDERS = (("bytes", "disk_cleanup_candidate_bytes"), ("unused_days", "disk_cleanup_candidate_unused_days"))
# (who, direction): (counter, the labels a row keeps)
IO = {
    ("job", "read"): ("io_usage_job_read_bytes_total", "host, job_id"),
    ("job", "written"): ("io_usage_job_written_bytes_total", "host, job_id"),
    ("login", "read"): ("io_usage_login_read_bytes_total", "host, device"),
    ("login", "written"): ("io_usage_login_written_bytes_total", "host, device"),
}


def space(selector):
    """{figure: expr}: the person's bytes, files and levels by host and filesystem."""
    out = {}
    for figure, metric, kind in SPACE:
        matchers = selector + (f', kind="{kind}"' if kind else "")
        out[figure] = f"max by (host, filesystem) ({metric}{{{matchers}}})"
    return out


def folders(selector):
    """{figure: expr}: the person's folders worth cleaning, by host, filesystem and path."""
    return {figure: f"max by (host, filesystem, path) ({metric}{{{selector}}})" for figure, metric in FOLDERS}


def io(who, direction, selector, window, function="increase"):
    """The bytes (FUNCTION increase) or bytes a second (rate) of the person's jobs or
    logins over WINDOW, one series per job or per disk of a login."""
    counter, by = IO[(who, direction)]
    return f"sum by ({by}) ({function}({counter}{{{selector}}}[{window}]))"


NOTES = [
    "Sources, all read from Prometheus (nothing is measured here):\n"
    "  space    the disk quota metrics: bytes and files on each counted filesystem, the warn\n"
    "           level (a notice) and the limit (the kernel refuses the write past it).\n"
    "           The host itself answers `quota -s` with the live figures.\n"
    "  folders  the folders worth cleaning: unused past the stale period, the largest and\n"
    "           longest unused first, from the last daily scan.\n"
    "  I/O      each job's reads and writes through system calls over the window (page\n"
    "           cache and pipes included: a ranking of your jobs, not their disk or NFS\n"
    "           bytes) and each login's block I/O on the node's own disks, outside jobs.\n"
    'The Grafana dashboard "Storage / per person" shows the same series over time.',
]

COMMAND = Command(
    "disk-usage",
    "your space and files on each filesystem against their limits, folders worth cleaning, recent I/O",
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
        Opt(
            "--top", metavar="N", type=int, default=10, help="rows per table of folders and I/O (default 10)"
        ),
        opt_json("the figures as JSON, for scripts"),
    ],
    machine="--json",
    notes=NOTES,
    summary_ko="내 파일시스템별 사용량과 파일 수, 경고 수준과 한도, 정리하면 좋을 폴더, 최근 I/O",
)


def _series(api, expr, labels):
    """{(host, *LABELS): value} of the instant query EXPR."""
    return {
        (host_of(r["metric"]),) + tuple(r["metric"].get(n, "") for n in labels): float(r["value"][1])
        for r in api.query(expr)
    }


def collect(api, user, hours, top):
    """The person's figures: space rows, folders and I/O rows, each list sorted."""
    selector = f"user={quoted(user)}"
    figures = {}
    for figure, expr in space(selector).items():
        for k, v in _series(api, expr, ("filesystem",)).items():
            figures.setdefault(k, dict.fromkeys(f for f, _, _ in SPACE))[figure] = v
    rows = [{"where": where(*k), **f} for k, f in sorted(figures.items()) if f["used"] is not None]
    found = {}
    for figure, expr in folders(selector).items():
        for k, v in _series(api, expr, ("filesystem", "path")).items():
            found.setdefault(k, {})[figure] = v
    kept = [
        {
            "where": where(h, fs),
            "path": p,
            "bytes": f.get("bytes") or 0,
            "unused_days": int(f.get("unused_days") or 0),
        }
        for (h, fs, p), f in found.items()
    ]
    kept.sort(key=lambda c: (-(c["bytes"] * c["unused_days"]), c["path"]))
    out = {"user": user, "hours": hours, "space": rows, "folders": kept[:top], "jobs": [], "logins": []}
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
    return out


def _level(used, warn, hard):
    """The style of a usage against its levels: at the limit, past the warn level, else."""
    if hard and used >= hard:
        return "crit"
    return "warn" if warn and used > warn else "text"


def _count(n):
    return "-" if not n else f"{n:,.0f}"


def _bytes(n):
    return "-" if not n else size(n)


def _heading(theme, title, tally):
    """A section heading with nothing under it, its tally beside the title as a table's."""
    plain = "   ".join(t for t, _ in tally)
    return theme.section(title, 2 + width(theme.g["sec"]) + width(title) + 3 + width(plain), tally)


def render(data, theme):
    who = data["user"]
    out = []
    grid = Grid(theme, ["WHERE", "USED", "WARN", "LIMIT", "FILES", "WARN", "LIMIT"],
                align=["l", "r", "r", "r", "r", "r", "r"],
                groups=[("SPACE", 1, 3), ("FILES", 4, 6)])  # fmt: skip
    for r in data["space"]:
        grid.row(
            [
                (r["where"], "text"),
                (size(r["used"]), _level(r["used"], r["warn"], r["hard"])),
                (_bytes(r["warn"]), "muted"),
                (_bytes(r["hard"]), "muted"),
                (_count(r["files"]), _level(r["files"] or 0, r["file_warn"], r["file_hard"])),
                (_count(r["file_warn"]), "muted"),
                (_count(r["file_hard"]), "muted"),
            ]
        )
    title = f"disk usage of {who}"
    if data["space"]:
        out.append(grid.render(title, [("warn: a notice   limit: writes refused", "sub")]))
    else:
        out.append(_heading(theme, title, [(f"no space of {who} is counted on any filesystem", "muted")]))
    if data["folders"]:
        grid = Grid(theme, ["FOLDER", "SIZE", "UNUSED", "WHERE"], align=["l", "r", "r", "l"])
        for c in data["folders"]:
            grid.row([(c["path"], "text"), (size(c["bytes"]), "text"),
                      (f"{c['unused_days']}d", "text"), (c["where"], "muted")])  # fmt: skip
        out.append(grid.render("folders worth cleaning", [("unused the longest and largest first", "sub")]))
    for who_, label, heading, title_ in (
        ("jobs", "job_id", "JOB", "I/O of jobs"),
        ("logins", "device", "DISK", "I/O of logins"),
    ):
        rows = data[who_]
        tally = [(f"last {data['hours']}h", "sub")]
        if not rows:
            out.append(_heading(theme, title_, [*tally, ("none", "muted")]))
            continue
        grid = Grid(theme, [heading, "HOST", "READ", "WRITTEN"], align=["l", "l", "r", "r"])
        for r in rows:
            grid.row([(r[label], "text"), (r["host"], "muted"), (size(r["read"]), "text"),
                      (size(r["written"]), "text")])  # fmt: skip
        out.append(grid.render(title_, tally))
    return "\n".join(out) + "\n"


def main(argv=None):
    args, theme = COMMAND.parse(argv)

    def body():
        if args.hours < 1 or args.top < 1:
            print("disk-usage: --hours and --top take a whole number from 1", file=sys.stderr)
            return 2
        url = site_info().get("prometheus_url")
        if not url:
            print(f"disk-usage: no prometheus_url in {SITE_PATH}", file=sys.stderr)
            return 1
        try:
            data = collect(Prometheus(url), args.user or me(), args.hours, args.top)
        except PrometheusError as e:
            print(f"disk-usage: {e}", file=sys.stderr)
            return 1
        data["at"] = int(time.time())
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            sys.stdout.write(render(data, theme))
        return 0

    return run_main("disk-usage", body)


if __name__ == "__main__":
    sys.exit(main())
