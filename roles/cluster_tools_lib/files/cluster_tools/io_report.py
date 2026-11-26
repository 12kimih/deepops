"""io_report: the nodes that drive an NFS export's load, and the jobs that do most I/O on them.

    hot = collect(prometheus, mounts, share, minutes, top)
    user_key(item, user), admin_key(item)  -> notice keys (cluster_tools.notices)
    dm_text(item, rank, advice), admin_text(items)

MOUNTS ({mountpoint: {"read_bps", "write_bps"}}) is what the disk behind each export
serves, measured (roles/io_fairness' models). A node is hot for a mount and a direction
when what it read from (or wrote to) that export over the wire stayed above SHARE of it
for the whole of the last MINUTES minutes (host_mount:nfs_*_bytes:rate5m at its lowest
over the window, node_exporter's mountstats). For each hot node, its TOP jobs by their
estimated share of the node's NFS traffic in that direction over the window
(job:nfs_*_bytes:rate5m, roles/prometheus rules io-usage.yml: the node's NFS bytes split
by its jobs' syscall I/O, roles/io_usage), and the notices say it is an estimate. It
reads Prometheus and changes nothing; cluster_tools.usage_report --io tells the people
and the administrators. Python 3.9 or newer, standard library only.
"""

from .render import size

DIRECTIONS = {
    # direction: (recorded node rate, capacity key, recorded job rate, verb, preposition)
    "read": (
        "host_mount:nfs_read_bytes:rate5m",
        "read_bps",
        "job:nfs_read_bytes:rate5m",
        "reading",
        "from",
    ),
    "write": (
        "host_mount:nfs_write_bytes:rate5m",
        "write_bps",
        "job:nfs_write_bytes:rate5m",
        "writing",
        "to",
    ),
}


def quoted(value):
    """VALUE as a PromQL string literal."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def collect(prom, mounts, share, minutes, top):
    """[{host, mount, direction, rate, capacity, jobs: [(job_id, user, rate)]}], the
    hottest first."""
    items = []
    for mount, capacity in sorted(mounts.items()):
        for direction, (recorded, cap_key, job_rate, _, _) in DIRECTIONS.items():
            limit = share * capacity[cap_key]
            hot = prom.query(f"min_over_time({recorded}{{mountpoint={quoted(mount)}}}[{minutes}m]) > {limit}")
            for row in hot:
                host = row["metric"].get("host", "")
                ranked = prom.query(
                    f"topk({top}, sum by (job_id, user) (avg_over_time({job_rate}{{host={quoted(host)}}}[{minutes}m])))"
                )
                jobs = sorted(
                    (
                        (r["metric"].get("job_id", ""), r["metric"].get("user", ""), float(r["value"][1]))
                        for r in ranked
                    ),
                    key=lambda job: -job[2],
                )
                items.append({"host": host, "mount": mount, "direction": direction, "rate": float(row["value"][1]),
                              "capacity": capacity[cap_key], "minutes": minutes, "jobs": jobs})  # fmt: skip
    return sorted(items, key=lambda item: -item["rate"] / item["capacity"])


def user_key(item, user):
    return f"io {user} {item['host']} {item['mount']} {item['direction']}"


def admin_key(item):
    return f"io admin {item['host']} {item['mount']} {item['direction']}"


def rate(nbytes):
    return f"{size(nbytes)}/s"


def share_of(item):
    return round(100 * item["rate"] / item["capacity"])


# The DM's words for each direction: (what the node did, what the job did most of).
DIRECTIONS_KO = {"read": ("에서 읽고", "읽은"), "write": ("에 쓰고", "쓴")}


def dm_text(item, rank, advice):
    """The DM to the user of ITEM's jobs[RANK]."""
    job, _, job_rate = item["jobs"][rank]
    did, what = DIRECTIONS_KO[item["direction"]]
    lead = "가장 많이" if rank == 0 else f"{rank + 1}번째로 많이"
    return (
        f"지난 {item['minutes']}분 동안 `{item['host']}` 노드가 `{item['mount']}`{did} 있습니다. "
        f"{rate(item['rate'])}로, 그 뒤의 디스크가 클러스터 전체에 내주는 양의 {share_of(item)}%라 "
        f"모두의 `{item['mount']}` 작업이 느려집니다. 그 노드에서 {lead} {what} 잡이 `{job}`입니다 "
        f"({rate(job_rate)}: 그 노드가 공유 스토리지와 주고받은 양을 노드 안 잡들의 읽기, 쓰기 호출 양에 비례해 나눈 추정치입니다)."
        f"\n{advice}"
    )


def admin_text(items):
    """One line per hot node, for the administrators' channel."""
    lines = []
    for item in items:
        _, _, _, verb, prep = DIRECTIONS[item["direction"]]
        # No job ranked on a hot node means the per-job count is broken there, not that
        # nobody did it: no one could be told, so the line says what to look at.
        jobs = ", ".join(f"{job} ({user}, {rate(r)})" for job, user, r in item["jobs"]) or (
            "none counted, so nobody was told: io-usage counts no job there (alert IoUsageJobsMissing)"
        )
        lines.append(
            f"I/O: `{item['host']}` {verb} {prep} `{item['mount']}` at {rate(item['rate'])} "
            f"({share_of(item)}% of the disk) for {item['minutes']} min; top jobs: {jobs}"
        )
    return "\n".join(lines)
