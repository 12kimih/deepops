"""Storage / per person: one person's space and files against their levels, folders
worth cleaning and the I/O of their jobs and logins, over time.

The queries are usage's own (cluster_tools.disk_usage: space, folders; cluster_tools.io_load: io), with
the person the User variable picks, so the command and this dashboard read one
definition of the same series: roles/disk_quota's metrics, cluster_tools.cleanup's
folders and roles/io_usage's counters.
"""

import sys
from pathlib import Path

from grafana import dashboard, host_links, job_links, label_variable, query, row, table, timeseries

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from cluster_tools import disk_usage, io_load  # noqa: E402

PATH = "src/dashboards/storage-person.json"
PERSON = 'user=~"$user"'
WHERE = "{{host}}:{{filesystem}}"


def build():
    space, folders = disk_usage.space(PERSON), disk_usage.folders(PERSON)

    def levels(title, used, warn, hard, unit, description):
        return timeseries(
            title,
            [
                query(space[used], f"used {WHERE}"),
                query(space[warn], f"warn {WHERE}"),
                query(space[hard], f"limit {WHERE}"),
            ],
            unit=unit,
            legend="table",
            calcs=("lastNotNull", "max"),
            description=description,
        )

    def io(who, legend, description, links=()):
        return timeseries(
            f"I/O of {who}s",
            [
                query(io_load.io(who, "read", PERSON, "5m", "rate"), f"read {legend}"),
                query(io_load.io(who, "written", PERSON, "5m", "rate"), f"written {legend}"),
            ],
            unit="Bps",
            legend="table",
            calcs=("lastNotNull", "max", "mean"),
            description=description,
            links=links,
        )

    panels = [
        row("Space and files"),
        levels(
            "Space",
            "used",
            "warn",
            "hard",
            "bytes",
            "Bytes on each filesystem whose use is counted (roles/disk_quota), with the warn level "
            "(a notice) and the limit (the kernel refuses the write that would pass it).",
        ),
        levels(
            "Files",
            "files",
            "file_warn",
            "file_hard",
            "none",
            "Files (inodes) on each counted filesystem, with their warn level and limit.",
        ),
        row("Folders worth cleaning"),
        table(
            "Size",
            folders["bytes"],
            ["host", "filesystem", "path", "Size"],
            w=12,
            sort="Size",
            value=("Size", "bytes", None),
            description="Folders unused past the stale period, from the last daily scan (cluster_tools.cleanup).",
        ),
        table(
            "Days unused",
            folders["unused_days"],
            ["host", "filesystem", "path", "Days unused"],
            w=12,
            sort="Days unused",
            value=("Days unused", "d", None),
            description="How long each of those folders has gone unread and unwritten.",
        ),
        row("I/O"),
        io(
            "job",
            "{{job_id}} {{host}}",
            "Each job's reads and writes through system calls (rchar and wchar, roles/io_usage): "
            "page cache and pipes included, so a ranking of the jobs, not disk or NFS bytes.",
            links=[*job_links("${__field.labels.job_id}"), *host_links("${__field.labels.host}")],
        ),
        io(
            "login",
            "{{host}} {{device}}",
            "Each login's block I/O on the node's own disks, outside jobs (io.stat of its user slice).",
        ),
    ]
    return dashboard(
        "storage-person",
        "Storage / per person",
        panels,
        tags=["storage"],
        variables=[label_variable("user", "disk_quota_used_bytes", "user", label_text="User")],
        description="One person's space and files against their levels, folders worth cleaning, and "
        "their jobs' and logins' I/O: the figures usage disk and usage io print, over time.",
    )
