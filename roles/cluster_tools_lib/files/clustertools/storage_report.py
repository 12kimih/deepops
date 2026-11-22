"""storage_report: the daily disk usage report, from the disk quota metrics.

    data = collect(prometheus, now)        -> what every counted filesystem holds, or None
    (text, blocks), thread = render(data, who, top, people, mention)
    over_warn(data)                        -> the people past a warn level, with figures
    row_key(row)                           -> the key of a person's notices (clustertools.notices)

For each filesystem whose users' space is counted (roles/disk_quota, its textfile
metrics), the disk: how full, how much is free and how that changed in a day; its
largest users among PEOPLE (the roster's active logins), with their day's growth and
their levels, and everyone else (root, the services, people who left) on one line; the
projects (directory trees with one limit). Everyone past a warn level is named: the
warn level only notifies, and the hard limit is the one the kernel enforces, at once.
A person is told (mentioned, or sent a DM) when they first pass a level, every day at
a hard limit, and again every repeat_days while past the same warn level; the card
lists everyone past a level every day. It reads Prometheus and changes nothing;
clustertools.usage_report --storage posts it.
Python 3.9 or newer, standard library only.
"""

from datetime import datetime

from .render import size
from .slack import MAX_BLOCKS, bar, sections, table

DAY = 86400
# When a host's usage is older than this, the report says so (the alert fires at an hour).
STALE = 3600
# A daily timer runs a little earlier or later each day: a notice due every N days may
# go this much early (clustertools.notices), so a weekly one never slips to the eighth day.
LEEWAY = 3600
HOW = "`quota -s` on the host shows your own usage there."

# The disks behind the counted filesystems: node_exporter's mount point is the quota
# metrics' filesystem.
_COUNTED = (
    "label_replace(count by (instance, filesystem) (disk_quota_used_bytes or disk_quota_project_used_bytes),"
    ' "mountpoint", "$1", "filesystem", "(.*)")'
)


def disk_query(metric):
    return f"max by (instance, host, mountpoint) ({metric}) and on (instance, mountpoint) {_COUNTED}"


def host_of(metric):
    """The host a series came from: its host label, else its instance without the port."""
    return metric.get("host") or metric.get("instance", "").rsplit(":", 1)[0]


def series(prom, expr, labels, at=None):
    """{(host, *LABELS' values): value} of the instant query EXPR."""
    return {
        (host_of(r["metric"]),) + tuple(r["metric"].get(name, "") for name in labels): float(r["value"][1])
        for r in prom.query(expr, at=at)
    }


def _usage(prom, now, used, limits, inode_used, inode_limits, labels):
    """{key: {used, before, warn, hard, inodes, inode_warn, inode_hard}}: the bytes now and
    a day ago (None when it was not counted then), and the levels set (0 none)."""
    out = {}
    before = series(prom, used, labels, at=now - DAY)
    for k, v in series(prom, used, labels).items():
        out[k] = {"used": v, "before": before.get(k), "warn": 0, "hard": 0,
                  "inodes": 0, "inode_warn": 0, "inode_hard": 0}  # fmt: skip
    for field, expr in (
        ("warn", f'{limits}{{kind="warn"}}'),
        ("hard", f'{limits}{{kind="hard"}}'),
        ("inodes", inode_used),
        ("inode_warn", f'{inode_limits}{{kind="warn"}}' if inode_limits else ""),
        ("inode_hard", f'{inode_limits}{{kind="hard"}}' if inode_limits else ""),
    ):
        if expr:
            for k, v in series(prom, expr, labels).items():
                if k in out:
                    out[k][field] = v
    return out


def collect(prom, now):
    """Everything the report shows, or None when no disk usage is counted anywhere."""
    users = _usage(
        prom, now, "disk_quota_used_bytes", "disk_quota_limit_bytes",
        "disk_quota_used_inodes", "disk_quota_limit_inodes", ("filesystem", "user"),
    )  # fmt: skip
    projects = _usage(
        prom, now, "disk_quota_project_used_bytes", "disk_quota_project_limit_bytes",
        "", "", ("filesystem", "project"),
    )  # fmt: skip
    if not users and not projects:
        return None
    sizes = series(prom, disk_query("node_filesystem_size_bytes"), ("mountpoint",))
    avail = series(prom, disk_query("node_filesystem_avail_bytes"), ("mountpoint",))
    avail_before = series(prom, disk_query("node_filesystem_avail_bytes"), ("mountpoint",), at=now - DAY)
    counted = {k[:2] for k in users} | {k[:2] for k in projects}
    disks = {
        k: {"size": sizes.get(k), "avail": avail.get(k), "avail_before": avail_before.get(k)}
        for k in sorted(counted)
    }
    reported = {k[0]: v for k, v in series(prom, "disk_quota_last_success_timestamp_seconds", ()).items()}
    return {"now": now, "disks": disks, "users": users, "projects": projects, "reported": reported}


def past(entry):
    """What of ENTRY is past its levels: [(what, used, level, kind)], hard first."""
    out = []
    for kind in ("hard", "warn"):
        for what, used, level in (
            ("space", entry["used"], entry[kind]),
            ("files", entry["inodes"], entry[f"inode_{kind}"]),
        ):
            # At the hard limit the kernel has refused the write that would pass it.
            if level and (used >= level if kind == "hard" else used > level):
                if not any(w == what for w, _, _, _ in out):
                    out.append((what, used, level, kind))
    return out


def over_warn(data):
    """[(host, filesystem, user, entry, past)] for everyone past a level, worst first."""
    rows = [(*k, e, past(e)) for k, e in data["users"].items() if past(e)]
    return sorted(rows, key=lambda r: (r[4][0][3] != "hard", -r[3]["used"], r[:3]))


def row_key(row):
    """A row of over_warn's key in the notices' record: "host filesystem user"."""
    return " ".join(row[:3])


def where(host, filesystem):
    return f"{host}:{filesystem}"


def change(now, before):
    """A day's change of a usage: +3.2G, -512M, 0, or new (not counted a day ago)."""
    if before is None:
        return "new"
    d = now - before
    return "0" if abs(d) < 1024**2 else ("+" if d > 0 else "") + size(d)


def disk_line(key, disk):
    """One line on a disk: how full, what is free, how the free space changed in a day."""
    if not disk["size"] or disk["avail"] is None:
        return f"*{where(*key)}*  no disk figures (node_exporter)"
    used = disk["size"] - disk["avail"]
    line = f"*{where(*key)}*  `{bar(100 * used / disk['size'])}`  {size(disk['avail'])} free of {size(disk['size'])}"
    if disk["avail_before"] is not None:
        line += f", {change(used, disk['size'] - disk['avail_before'])} used in a day"
    return line


def _level(entry, kind):
    return size(entry[kind]) if entry[kind] else "-"


def describe(row):
    """(where, how much, how it stands, at the hard limit) for a row of over_warn."""
    host, filesystem, _, entry, levels = row
    what, used, level, kind = levels[0]
    figure = size if what == "space" else (lambda n: f"{n:,.0f} files")
    if kind == "hard":
        state = f"at the hard limit {figure(level)}: writes there fail now"
    else:
        hard = entry["hard"] if what == "space" else entry["inode_hard"]
        state = f"past the warn level {figure(level)}" + (f", hard limit {figure(hard)}" if hard else "")
    return where(host, filesystem), figure(used), state, kind == "hard"


def hold_note(hold, now):
    """What a hard limit means for Slurm (clustertools.submit_holds): HOLD is None (no
    holds) or the Unix time they start."""
    if hold is None:
        return ""
    if now >= hold:
        return "new Slurm jobs are refused until you free space"
    return f"from {datetime.fromtimestamp(hold):%Y-%m-%d}, new Slurm jobs are refused while at it"


def past_line(row, who, mention=True, note=""):
    place, amount, state, hard = describe(row)
    if hard and note:
        state += f"; {note}"
    return f"{':no_entry:' if hard else ':warning:'} {who(row[2], mention)} on {place}: {amount}, {state}"


def others_line(rest, shown=3):
    """Everyone not in the roster, on one line: the largest SHOWN by name, then the rest."""
    names = ", ".join(f"{u} {size(e['used'])}" for u, e in rest[:shown])
    more = f", {len(rest) - shown} more" if len(rest) > shown else ""
    return f"Not in the roster (root, services, people who left): {names}{more}; {size(sum(e['used'] for _, e in rest))} together"


def render(data, who, top, people=(), mention=None, hold=None):
    """The summary card (text, blocks) and the thread [(text, blocks)]: every disk and
    everyone past a level on the card; each filesystem's TOP people, everyone else, and
    the projects in the thread. With no PEOPLE (no roster) every user is ranked. Of those
    past a level, the keys in MENTION (row_key) are mentioned; None mentions them all.
    HOLD (hold_note) says what a hard limit means for their Slurm jobs."""
    stamp = datetime.fromtimestamp(data["now"]).strftime("%Y-%m-%d")
    rows = over_warn(data)
    full = []
    for key, disk in data["disks"].items():
        if disk["size"] and disk["avail"] is not None:
            full.append(f"{where(*key)} {100 * (disk['size'] - disk['avail']) / disk['size']:.0f}%")
    text = f"Disk usage {stamp}: " + (", ".join(full) or "no disk figures")
    if rows:
        text += f"; {len(rows)} past their level"
    card = [{"type": "header", "text": {"type": "plain_text", "text": f"Disk usage {stamp}"}}]
    card += sections("\n".join(disk_line(k, d) for k, d in data["disks"].items()))
    if rows:
        card += sections(
            "*Past a level*\n"
            + "\n".join(
                past_line(r, who, mention is None or row_key(r) in mention, hold_note(hold, data["now"]))
                for r in rows
            )
        )
    stale = sorted(
        f"{h} (last {datetime.fromtimestamp(t).strftime('%m-%d %H:%M')})"
        for h, t in data["reported"].items()
        if data["now"] - t > STALE
    )
    notes = [f"The warn level only notifies; the hard limit refuses writes at once. {HOW}"]
    if stale:
        notes.append(f"Not reported lately, so older figures: {', '.join(stale)}.")
    card.append({"type": "context", "elements": [{"type": "mrkdwn", "text": " ".join(notes)}]})

    detail = []
    for key in data["disks"]:
        everyone = sorted(
            ((u, e) for (h, f, u), e in data["users"].items() if (h, f) == key),
            key=lambda r: (-r[1]["used"], r[0]),
        )
        if not everyone:
            continue
        mine = [r for r in everyone if not people or r[0] in people]
        others = [r for r in everyone if people and r[0] not in people]
        shown = mine[:top]
        body = [
            (u, size(e["used"]), change(e["used"], e["before"]), _level(e, "warn"), _level(e, "hard"))
            for u, e in shown
        ]
        tail = ""
        if len(mine) > len(shown):
            rest = mine[len(shown) :]
            tail = f"\n{len(rest)} more, {size(sum(e['used'] for _, e in rest))} together"
        if others:
            tail += "\n" + others_line(others)
        head = f":file_cabinet: *{where(*key)}*, the largest of {len(mine)} {'people' if people else 'users'}"
        detail += sections(f"{head}\n{table(('USER', 'USED', 'DAY', 'WARN', 'HARD'), body)}{tail}")
    if data["projects"]:
        body = [
            (
                p,
                where(h, f),
                size(e["used"]),
                change(e["used"], e["before"]),
                _level(e, "warn"),
                _level(e, "hard"),
            )
            for (h, f, p), e in sorted(data["projects"].items())
        ]
        head = ":open_file_folder: *Projects* (a directory tree with one limit)"
        detail += sections(f"{head}\n{table(('PROJECT', 'ON', 'USED', 'DAY', 'WARN', 'HARD'), body)}")
    thread = [("Disk usage details", detail[i : i + MAX_BLOCKS]) for i in range(0, len(detail), MAX_BLOCKS)]
    return (text, card), thread


def dm_text(row, note=""):
    """The note to someone past a level: where, how much, what it means (NOTE, hold_note,
    at a hard limit) and what to do."""
    place, amount, state, hard = describe(row)
    if hard:
        act = "Writes there fail" + (f" and {note}" if note else " until you free space") + "."
    else:
        act = "Nothing is refused yet: free space before the hard limit."
    return f"Your disk use on {place} is {amount}, {state}. {act} {HOW}"
