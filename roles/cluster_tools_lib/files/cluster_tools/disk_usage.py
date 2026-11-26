"""usage disk: your space on each filesystem against its levels, your folders worth
cleaning, and your files on the scratch space due to be cleaned up, with their dates.
The exit status is the disk and scratch rules' for you (cluster_tools.rules).

All from Prometheus (prometheus_url in the site file), the series the cluster already
keeps; nothing is measured here:
  space    roles/disk_quota's metrics: the bytes and files each person holds on each
           counted filesystem, the warn level (a notice) and the hard limit (the kernel
           refuses the write that would pass it)
  folders  the folders worth cleaning (cluster_tools.cleanup: unused past the stale
           period), the largest and longest unused first
and the scratch cleanup's list of your files (cluster_tools.scratch_space), each looked
at again, so one used since shows as spared. Your I/O: usage io.
The Grafana dashboard "Storage / per person" (scripts/dashboards/storage_person.py)
draws the same queries (space, folders below) over time, for the person picked.
Python 3.9 or newer, standard library only.
"""

import os
import sys
import time

from . import rules
from .usage import JSON_NOTE
from .cli import UNKNOWN, Command, Opt, opt_json, opt_user, run_main
from .disk_report import USAGE, host_of, where
from .io_report import quoted
from .render import Grid, size, width

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
# When each filesystem's folders were last weighed (cluster_tools.cleanup): the folders
# are as of then, and each filesystem is scanned on its own schedule.
SCANNED = "max by (host, filesystem) (disk_cleanup_scan_timestamp_seconds)"


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


NOTES = [
    "Sources, all read from Prometheus (nothing is measured here):\n"
    "  space    the disk quota metrics: bytes and files on each counted filesystem, the warn\n"
    "           level (a notice) and the limit (the kernel refuses the write past it).\n"
    "           The host itself answers `quota -s` with the live figures.\n"
    "  folders  the folders worth cleaning: unused past the stale period, the largest and\n"
    "           longest unused first, from each filesystem's last scan (its time shown; the\n"
    "           scans run on their own schedules, not daily everywhere).\n"
    "  scratch  the scratch cleanup's list of your files unused for its stale period, each\n"
    "           with the date it goes unless you use it (read or write) before.\n"
    'The Grafana dashboard "Storage / per person" shows the same series over time.',
]

COMMAND = Command(
    USAGE,
    "your space and files against their limits, folders worth cleaning, scratch files due to go",
    [
        opt_user("someone else's figures instead of yours", metavar="NAME"),
        Opt(
            "--top",
            metavar="N",
            type=int,
            default=10,
            help="rows per table of folders and scratch files (default 10)",
        ),
        opt_json("one object, schema 1 (below): space, folders, scans, scratch"),
    ],
    usage=f"{USAGE} [OPTIONS]",
    machine="--json",
    notes=[*NOTES, JSON_NOTE],
    summary_ko="내 용량과 파일 수, 경고 수준과 한도, 정리하면 좋을 폴더, 정리될 스크래치 파일",
)


def _series(api, expr, labels):
    """{(host, *LABELS): value} of the instant query EXPR."""
    return {
        (host_of(r["metric"]),) + tuple(r["metric"].get(n, "") for n in labels): float(r["value"][1])
        for r in api.query(expr)
    }


def collect(api, user, top):
    """The person's figures: space rows and folders, each list sorted."""
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
    scans = [
        {"where": where(h, fs), "time": int(v)}
        for (h, fs), v in sorted(_series(api, SCANNED, ("filesystem",)).items())
    ]
    return {"user": user, "space": rows, "folders": kept[:top], "scans": scans}


def _level(used, warn, hard):
    """The style of a usage against its levels: at the limit, past the warn level, else."""
    if hard and used >= hard:
        return "crit"
    return "warn" if warn and used > warn else "text"


def _count(n):
    return "-" if not n else f"{n:,.0f}"


def _bytes(n):
    return "-" if not n else size(n)


def _when(seconds):
    """A scan's time, local, to the minute."""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(seconds))


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
        shown = {c["where"] for c in data["folders"]}
        scanned = [f"{s['where']} {_when(s['time'])}" for s in data.get("scans", []) if s["where"] in shown]
        tally = [("unused the longest and largest first", "sub")]
        if scanned:
            tally.append(("scanned " + ", ".join(scanned), "muted"))
        out.append(grid.render("folders worth cleaning", tally))
    return "\n".join(out) + "\n"


def scratch(user, cfg, top, now):
    """The scratch cleanup's view of USER's files (cluster_tools.scratch_space), or "":
    no scratch space on this cluster, or none mounted here."""
    from . import scratch_space

    part = cfg.get("scratch")
    if not part or not os.path.isdir(part["root"]):
        return ""
    text, _ = scratch_space.show(scratch_space.Host(), part["root"], user, now, top=top)
    return "\n" + text


def main(argv=None):
    from .usage import context, emit, table

    args, theme = COMMAND.parse(argv)

    def body():
        if args.top < 1:
            print(f"{USAGE}: --top takes a whole number from 1", file=sys.stderr)
            return 2
        user, _, cfg, api = context(args.user)
        if api is None:
            print(f"{USAGE}: no prometheus_url", file=sys.stderr)
            return UNKNOWN
        now = time.time()
        data = collect(api, user, args.top)
        rows = [rules.disk(user, cfg, api, now), rules.scratch(user, cfg, now)]
        text = (
            table(rows, theme, f"space of {user}") + render(data, theme) + scratch(user, cfg, args.top, now)
        )
        data.pop("user", None)
        return emit(USAGE, user, rows, args, theme, text, **data)

    return run_main(USAGE, body, unknown=UNKNOWN)


if __name__ == "__main__":
    sys.exit(main())
