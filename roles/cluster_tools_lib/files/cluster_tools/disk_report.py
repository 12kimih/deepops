"""disk_report: the daily disk usage report, from the disk quota metrics.

    data = collect(prometheus, now)        -> what every counted filesystem holds, or None
    (text, blocks), thread = render(data, who, people=..., mention=..., fill=...)
    fill_rows(data, fraction, people, n)   -> [(disk key, fill, [(user, used)])] of the
                                              filesystems at least FRACTION full
    fill_key(disk key)                     -> the key of a filesystem's fill notice
    over_warn(data)                        -> the people past a warn level, with figures
    row_key(row)                           -> the key of a person's notices (cluster_tools.notices)
    purge = collect_purge(prometheus)      -> each person's files the scratch cleanup lists
    purge_key(user, entry), purge_dm_text(user, entry), burst_text(host, user, files)

For each filesystem whose users' space is counted (roles/disk_quota, its textfile
metrics), the disk: how full, how much is free and how that changed in a day; its
users among PEOPLE (the roster's active logins), every one, largest first, with their
day's growth and their levels, and everyone else (root, the services, people who left)
in a table of their own; the
projects (directory trees with one limit). Everyone past a warn level is named: the
warn level only notifies, and the hard limit is the one the kernel enforces, at once.
A person is told (mentioned, or sent a DM) when they first pass a level, every day at
a hard limit, and again every repeat_days while past the same warn level; the card
lists everyone past a level every day. A filesystem at least the fill fraction full
names its largest people on the card every day, with a mention when its fill notice is
due (cluster_tools.notices: at most every repeat_days). The thread lists each
filesystem's folders worth cleaning (cluster_tools.cleanup's metrics: unused past the
stale period, the largest and longest unused first), never with a mention. It reads
Prometheus and changes nothing;
cluster_tools.usage_report --disk posts it.
Python 3.9 or newer, standard library only.
"""

from datetime import datetime

from . import cards, rules
from .render import size
from .cards import gauges
from .slack import bar, pages, table

DAY = 86400
# When a host's usage is older than this, the report says so (the alert fires at an hour).
STALE = 3600
# A daily timer runs a little earlier or later each day: a notice due every N days may
# go this much early (cluster_tools.notices), so a weekly one never slips to the eighth day.
LEEWAY = 3600
# The command that shows a person their own use, on any host (cluster_tools.disk_usage):
# every notice and refusal about disk use names it.
USAGE = rules.BY_ID["disk"].check
HOW = f"내 사용량은 `{USAGE}`로 보실 수 있습니다."

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
    return {"now": now, "disks": disks, "users": users, "projects": projects, "reported": reported,
            "cleanup": collect_cleanup(prom)}  # fmt: skip


def collect_cleanup(prom):
    """{(host, filesystem): [{path, user, bytes, unused_days}]}, the most worth cleaning
    first (cluster_tools.cleanup's metrics, from whichever host scanned)."""
    nbytes = series(prom, "disk_cleanup_candidate_bytes", ("filesystem", "path", "user"))
    unused = series(prom, "disk_cleanup_candidate_unused_days", ("filesystem", "path", "user"))
    out = {}
    for k, v in nbytes.items():
        out.setdefault(k[:2], []).append(
            {"path": k[2], "user": k[3], "bytes": v, "unused_days": int(unused.get(k, 0))}
        )
    for rows in out.values():
        rows.sort(key=lambda c: (-(c["bytes"] * c["unused_days"]), c["path"]))
    return out


def fill_rows(data, fraction, people=(), n=5):
    """[(disk key, fill (0-1), [(user, used)])]: each filesystem at least FRACTION full,
    with its N largest users among PEOPLE (everyone without a roster)."""
    out = []
    for key, disk in data["disks"].items():
        if not disk["size"] or disk["avail"] is None:
            continue
        fill = (disk["size"] - disk["avail"]) / disk["size"]
        if fill < fraction:
            continue
        users = sorted(
            (
                (u, e["used"])
                for (h, f, u), e in data["users"].items()
                if (h, f) == key and (not people or u in people)
            ),
            key=lambda r: (-r[1], r[0]),
        )
        out.append((key, fill, users[:n]))
    return out


def fill_key(key):
    """A filesystem's fill notice in the notices' record: "fill host filesystem"."""
    return "fill " + " ".join(key)


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


def projects_past(data):
    """Lines for the card: each project past a level (the shared data's budget among
    them, cluster_tools.shared_data), the hard limit first."""
    rows = sorted(
        ((k, e, past(e)[0]) for k, e in data["projects"].items() if past(e)),
        key=lambda r: (r[2][3] != "hard", -r[1]["used"], r[0]),
    )
    lines = []
    for (host, fs, project), entry, (_, used, level, kind) in rows:
        if kind == "hard":
            state = f"한도({size(level)})에 닿아 쓰기가 막혀 있습니다"
        else:
            state = f"경고 수준({size(level)})을 넘었습니다" + (
                f" (한도 {size(entry['hard'])})" if entry["hard"] else ""
            )
        lines.append(
            f"{cards.mark(state_of(kind))}**{project}** {where(host, fs)}에서: {size(used)}, {state}"
        )
    return lines


def state_of(kind):
    """The rules state of a level reached (cluster_tools.rules): a hard limit acts, a
    warning level warns."""
    return "act" if kind == "hard" else "warn"


def row_key(row):
    """A row of over_warn's key in the notices' record: "host filesystem user"."""
    return " ".join(row[:3])


def where(host, filesystem):
    return f"{host}:{filesystem}"


def change(now, before):
    """A day's change of a usage: +3.2G, -512M, 0, or 새로 (not counted a day ago)."""
    if before is None:
        return "신규"
    d = now - before
    return "0" if abs(d) < 1024**2 else ("+" if d > 0 else "") + size(d)


DISK_HEADER = ("위치", "사용", "크기", "남음", "하루 사이")


def disk_row(key, disk):
    """A disk's row of the card's gauge table (DISK_HEADER): how full, its size, what is
    free and how its use changed in a day; dashes without figures (node_exporter)."""
    if not disk["size"] or disk["avail"] is None:
        return (where(*key), bar(None), "-", "-", "수치 없음")
    used = disk["size"] - disk["avail"]
    day = change(used, disk["size"] - disk["avail_before"]) if disk["avail_before"] is not None else "-"
    return (where(*key), bar(100 * used / disk["size"]), size(disk["size"]), size(disk["avail"]), day)


def _level(entry, kind):
    return size(entry[kind]) if entry[kind] else "-"


def amounts(row):
    """(where, what, used, level, kind, hard limit) of a row of over_warn, in no language:
    WHAT is space (bytes) or files, KIND warn or hard. The notices word them (describe),
    and so does the submit hold (cluster_tools.submit_holds)."""
    host, filesystem, _, entry, levels = row
    what, used, level, kind = levels[0]
    return (
        where(host, filesystem),
        what,
        used,
        level,
        kind,
        entry["hard"] if what == "space" else entry["inode_hard"],
    )


def describe(row):
    """(where, how much, how it stands, at the hard limit) for a row of over_warn: the
    state a sentence ("한도(5T)에 닿았습니다"), the level in parentheses so the particle
    follows the word, not the figure."""
    place, what, used, level, kind, hard = amounts(row)
    figure = size if what == "space" else (lambda n: f"파일 {n:,.0f}개")
    if kind == "hard":
        state = f"한도({figure(level)})에 닿았습니다"
    else:
        state = f"경고 수준({figure(level)})을 넘었습니다" + (f" (한도 {figure(hard)})" if hard else "")
    return place, figure(used), state, kind == "hard"


def hold_note(hold, now):
    """What a hard limit means for Slurm (cluster_tools.submit_holds): HOLD is None (no
    holds) or the Unix time they start."""
    if hold is None:
        return ""
    if now >= hold:
        return "공간을 비우실 때까지 새 Slurm 잡을 제출할 수 없습니다"
    return f"{datetime.fromtimestamp(hold):%Y-%m-%d}부터는 한도에 닿아 있는 동안 새 Slurm 잡을 제출할 수 없습니다"


def past_line(row, who, mention=True, note=""):
    place, amount, state, hard = describe(row)
    if hard and note:
        state += f". {note}"
    return f"{cards.mark('act' if hard else 'warn')}{who(row[2], mention)} {place}에서: {amount}, {state}"


def render(data, who, *, people=(), mention=None, hold=None, fill=None, fill_mention=(), stale_days=0):
    """The summary card (text, blocks) and the thread [(text, blocks)]: every disk and
    everyone past a level on the card; each filesystem's people, every one, everyone else, the
    projects and the folders worth cleaning in the thread. With no PEOPLE (no roster)
    every user is ranked. Of those past a level, the keys in MENTION (row_key) are
    mentioned; None mentions them all. HOLD (hold_note) says what a hard limit means for
    their Slurm jobs. FILL (fill_rows) names the largest people of each fuller
    filesystem, mentioned where its fill_key is in FILL_MENTION."""
    stamp = datetime.fromtimestamp(data["now"]).strftime("%Y-%m-%d")
    rows = over_warn(data)
    full = []
    for key, disk in data["disks"].items():
        if disk["size"] and disk["avail"] is not None:
            full.append(f"{where(*key)} {100 * (disk['size'] - disk['avail']) / disk['size']:.0f}%")
    text = f"디스크 사용량 {stamp}: " + (", ".join(full) or "디스크 수치 없음")
    if rows:
        text += f", 경고 수준을 넘은 사용량 {len(rows)}건"
    card = [cards.title("디스크 사용량", stamp)]
    card += cards.part("디스크", gauges(DISK_HEADER, [disk_row(k, d) for k, d in data["disks"].items()]))
    if rows:
        card += cards.part(
            "경고 수준을 넘은 사용량",
            "\n".join(
                past_line(r, who, mention is None or row_key(r) in mention, hold_note(hold, data["now"]))
                for r in rows
            ),
        )
    shared = projects_past(data)
    if shared:
        card += cards.part("경고 수준을 넘은 프로젝트", "\n".join(shared))
    if fill:
        card += cards.part(
            "가득 차 가는 공간",
            "가장 많이 쓰시는 분들께 부탁드립니다. 비울 수 있는 것을 비워 주세요.\n"
            + "\n".join(
                f"{cards.mark('warn')}**{where(*key)}** {100 * frac:.0f}% 사용 중: "
                + ", ".join(f"{who(u, fill_key(key) in fill_mention)} {size(used)}" for u, used in users)
                for key, frac, users in fill
            ),
        )
    stale = sorted(
        f"{h} (마지막 {datetime.fromtimestamp(t).strftime('%m-%d %H:%M')})"
        for h, t in data["reported"].items()
        if data["now"] - t > STALE
    )
    notes = ["경고 수준을 넘으면 알려 드리기만 하고, 한도에 닿으면 바로 쓰기가 막힙니다."]
    if stale:
        notes.append(f"최근 보고가 없어 예전 수치입니다: {', '.join(stale)}.")
    card.append(cards.note(" ".join(notes)))
    card += cards.step(f"{HOW} 사람별 사용량과 정리하면 좋을 폴더는 이 메시지의 스레드에 있습니다.")

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
        count = (lambda n: f"{n}명") if people else (lambda n: f"계정 {n}개")
        if mine:
            body = [
                (u, size(e["used"]), change(e["used"], e["before"]), _level(e, "warn"), _level(e, "hard"))
                for u, e in mine
            ]
            detail += cards.part(
                f"{where(*key)}: {count(len(mine))}, 많이 쓰는 순",
                table(("사용자", "사용량", "하루", "경고", "한도"), body),
            )
        if others:
            body = [(u, size(e["used"]), change(e["used"], e["before"])) for u, e in others]
            total = size(sum(e["used"] for _, e in others))
            detail += cards.part(
                f"{where(*key)}: 명부에 없는 계정 (root, 서비스, 떠난 분) {len(others)}개, 합계 {total}",
                table(("계정", "사용량", "하루"), body),
            )
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
        detail += cards.part(
            "프로젝트 (한도 하나를 함께 쓰는 디렉터리)",
            table(("프로젝트", "위치", "사용량", "하루", "경고", "한도"), body),
        )
    for key, rows in sorted((data.get("cleanup") or {}).items()):
        body = [(c["path"], c["user"], size(c["bytes"]), f"{c['unused_days']}일") for c in rows]
        detail += cards.part(
            f"{where(*key)}: 정리하면 좋을 폴더 ({stale_days}일 넘게 쓰지 않은 것, 크고 오래된 순)",
            table(("폴더", "주인", "크기", "안 쓴 기간"), body),
        )
    thread = pages("디스크 사용량 자세히", detail)
    return (text, card), thread


def dm_text(row, note=""):
    """The note to someone past a level: where, how much, what it means (NOTE, hold_note,
    at a hard limit) and what to do."""
    place, amount, state, hard = describe(row)
    if hard:
        act = "지금은 그곳에 쓸 수 없습니다. " + (
            f"{note}." if note else "공간을 비우시면 다시 쓰실 수 있습니다."
        )
    else:
        act = "아직 막힌 것은 없습니다. 한도에 닿기 전에 공간을 비워 주세요."
    return f"{place}의 디스크 사용량이 {amount}입니다. {state}. {act}\n{HOW}"


# The scratch space's cleanup (cluster_tools.scratch_space): each person's files listed for
# removal, told once per removal date, and the mass access-time refreshes an
# administrator sees.
PURGE_HOW = f"`{rules.BY_ID['scratch-purge'].check}`로 정리될 파일과, 그사이 쓰셔서 정리 대상에서 빠진 파일을 보실 수 있습니다."


def collect_purge(prom):
    """{"due": {(host, user): {files, bytes, next, stale_days}}, "bursts": {(host, user): files}}."""
    files = series(prom, "scratch_space_purge_due_files", ("user",))
    nbytes = series(prom, "scratch_space_purge_due_bytes", ("user",))
    nxt = series(prom, "scratch_space_purge_next_timestamp_seconds", ("user",))
    held = series(prom, "scratch_space_purge_deferred_files", ("user",))
    stale = {k[0]: v for k, v in series(prom, "scratch_space_purge_stale_days", ()).items()}
    due = {
        k: {
            "files": int(v),
            "bytes": nbytes.get(k, 0),
            "next": nxt.get(k),
            "stale_days": int(stale.get(k[0], 0)),
            "deferred": int(held.get(k, 0)),
        }
        for k, v in files.items()
        if v > 0 and nxt.get(k)
    }
    bursts = {
        k: int(v) for k, v in series(prom, "scratch_space_purge_burst_files", ("user",)).items() if v > 0
    }
    return {"due": due, "bursts": bursts}


def purge_key(k, entry):
    """A person is told once per removal date: a new date is a new notice."""
    return f"purge {k[0]} {k[1]} {datetime.fromtimestamp(entry['next']).strftime('%Y-%m-%d')}"


def purge_dm_text(k, entry):
    when = datetime.fromtimestamp(entry["next"]).strftime("%Y-%m-%d")
    return (
        f"스크래치 공간의 파일 {entry['files']}개({size(entry['bytes'])})를 {entry['stale_days']}일 동안"
        f" 쓰지 않으셨습니다. {when}부터 차례로 정리되니, 남겨 두실 파일은 그전에 한 번 읽거나 써 주세요."
        + (
            f" 그중 {entry['deferred']}개는 실행 중인 잡이 있어 미뤄졌습니다. 잡이 계속 실행 중이어도 미뤄진 날짜에는"
            " 정리되니, 남겨 두실 파일은 그전에 써 주세요."
            if entry.get("deferred")
            else ""
        )
        + f" 스크래치 공간은 지금 쓰는 데이터를 위한 곳이니, 오래 남길 것은 홈에 두세요.\n{PURGE_HOW}"
    )


def burst_text(k, files):
    return (
        f"Scratch space on {k[0]}: {files} old files of {k[1]} were all used within minutes,"
        " as a mass access-time refresh would (to escape the cleanup, against the rules). Nothing"
        " is done automatically; a look is up to you."
    )
