"""io_report: the nodes that drive an NFS export's load, and the jobs that do most I/O on them.

    hot = collect(prometheus, mounts, share, minutes, top)
    user_key(item, user), admin_key(item)  -> notice keys (cluster_tools.notices)
    dm_text(item, rank, advice), admin_text(items)

MOUNTS ({mountpoint: {"read_bps", "write_bps"}}) is what the disk behind each export
serves, measured (roles/io_fairness' models). A node is hot for a mount and a direction
when what it read from (or wrote to) that export over the wire stayed above SHARE of it
for the whole of the last MINUTES minutes (host_mount:nfs_*_bytes:rate5m at its lowest
over the window, node_exporter's mountstats). For each hot node, its TOP jobs by the
same direction's syscall I/O over the window (roles/io_usage: io_usage_job_*_bytes_total):
a ranking within the node, page cache and pipes included, never a job's NFS bytes, and
the notices say so. It reads Prometheus and changes nothing; cluster_tools.usage_report
--io tells the people and the administrators. Python 3.9 or newer, standard library only.
"""

from .render import size

DIRECTIONS = {
    # direction: (recorded node rate, capacity key, job counter, verb, preposition)
    "read": (
        "host_mount:nfs_read_bytes:rate5m",
        "read_bps",
        "io_usage_job_read_bytes_total",
        "reading",
        "from",
    ),
    "write": (
        "host_mount:nfs_write_bytes:rate5m",
        "write_bps",
        "io_usage_job_written_bytes_total",
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
        for direction, (recorded, cap_key, counter, _, _) in DIRECTIONS.items():
            limit = share * capacity[cap_key]
            hot = prom.query(f"min_over_time({recorded}{{mountpoint={quoted(mount)}}}[{minutes}m]) > {limit}")
            for row in hot:
                host = row["metric"].get("host", "")
                ranked = prom.query(
                    f"topk({top}, sum by (job_id, user) (rate({counter}{{host={quoted(host)}}}[{minutes}m])))"
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


def dm_text(item, rank, advice):
    """The DM to the user of ITEM's jobs[RANK]."""
    job, _, job_rate = item["jobs"][rank]
    _, _, _, verb, prep = DIRECTIONS[item["direction"]]
    lead = "did the most" if rank == 0 else f"was number {rank + 1} of the jobs by"
    return (
        f"For the last {item['minutes']} minutes, node `{item['host']}` has been {verb} {prep} "
        f"`{item['mount']}` at {rate(item['rate'])}, {share_of(item)}% of what the disk behind it serves "
        f"the whole cluster, so everyone's work on `{item['mount']}` is slower. Your job `{job}` {lead} "
        f"{verb} on that node ({rate(job_rate)} through its read and write calls, cache and pipes "
        f"included, so a ranking on the node, not its own share of the disk).\n{advice}"
    )


def admin_text(items):
    """One line per hot node, for the administrators' channel."""
    lines = []
    for item in items:
        _, _, _, verb, prep = DIRECTIONS[item["direction"]]
        jobs = ", ".join(f"{job} ({user}, {rate(r)})" for job, user, r in item["jobs"]) or "no job counted"
        lines.append(
            f"I/O: `{item['host']}` {verb} {prep} `{item['mount']}` at {rate(item['rate'])} "
            f"({share_of(item)}% of the disk) for {item['minutes']} min; top jobs: {jobs}"
        )
    return "\n".join(lines)
