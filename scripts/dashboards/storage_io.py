"""Storage / NFS and disks: who drives the NFS exports and the server's disks, before and after iocost.

From node_exporter's mountstats (each node's NFS traffic, recorded per node and mount
point by roles/prometheus rules io-usage.yml), roles/io_usage's counters (each job's and
login's I/O, each slice's waits), roles/io_fairness' io_fairness_enabled and
node_exporter's disk counters. Set the time range across the moment io_fairness_enabled
turns 1 to compare the same panels before and after.
"""

from grafana import dashboard, host_links, job_links, query, row, timeseries

PATH = "src/dashboards/storage-io.json"

JOB_NFS = "job:nfs_read_bytes:rate5m + job:nfs_write_bytes:rate5m"
LOGIN_IO = "rate(io_usage_login_read_bytes_total[5m]) + rate(io_usage_login_written_bytes_total[5m])"
# The disks iocost is set on, by host and kernel name, for joining node_exporter's.
SHARED = "max by (host, device) (io_fairness_enabled)"


def build():
    panels = [
        row("NFS exports"),
        timeseries(
            "NFS reads by node",
            [query("host_mount:nfs_read_bytes:rate5m", "{{host}} {{mountpoint}}")],
            unit="Bps",
            legend="table",
            calcs=("lastNotNull", "max", "mean"),
            links=host_links("${__field.labels.host}"),
            description="Bytes each node read from each NFS export over the wire (node_exporter mountstats).",
        ),
        timeseries(
            "NFS writes by node",
            [query("host_mount:nfs_write_bytes:rate5m", "{{host}} {{mountpoint}}")],
            unit="Bps",
            legend="table",
            calcs=("lastNotNull", "max", "mean"),
            links=host_links("${__field.labels.host}"),
            description="Bytes each node wrote to each NFS export over the wire.",
        ),
        timeseries(
            "NFS read and write response by node",
            [
                query(
                    'host_mount_operation:nfs_response_seconds:mean5m{operation=~"READ|WRITE"}',
                    "{{host}} {{mountpoint}} {{operation}}",
                )
            ],  # fmt: skip
            unit="s",
            legend="table",
            calcs=("lastNotNull", "max", "mean"),
            links=host_links("${__field.labels.host}"),
            description=(
                "Each node's mean wait for the server to answer a READ or WRITE, per mount, over 5 "
                "minutes (node_exporter mountstats). All nodes slow at once: the server or its disks; "
                "one alone: its network or its own queue. NfsClientSlow fires past "
                "prometheus_nfs_client_slow_ms."
            ),
        ),
        timeseries(
            "NFS metadata response by node",
            [
                query(
                    'host_mount_operation:nfs_response_seconds:mean5m{operation!~"READ|WRITE"}',
                    "{{host}} {{mountpoint}} {{operation}}",
                )
            ],  # fmt: skip
            unit="s",
            legend="table",
            calcs=("lastNotNull", "max", "mean"),
            links=host_links("${__field.labels.host}"),
            description=(
                "The same for the lookups and attribute reads (GETATTR, LOOKUP, ACCESS, COMMIT: "
                "prometheus_nfs_client_operations): slow ones are what makes ls and imports hang."
            ),
        ),
        timeseries(
            "NFS queue share by node",
            [query("host_mount_operation:nfs_queue_share:ratio5m", "{{host}} {{mountpoint}} {{operation}}")],
            unit="percentunit",
            max=1,
            legend="table",
            calcs=("lastNotNull", "max", "mean"),
            links=host_links("${__field.labels.host}"),
            description=(
                "The share of each request's time spent waiting in the node's own queue before it was "
                "sent: high means the node is the bottleneck (its RPC slots), not the server."
            ),
        ),
        row("Jobs and logins"),
        timeseries(
            "Top jobs by NFS traffic (estimated)",
            [query(f"topk(10, sum by (host, job_id, user) ({JOB_NFS}))", "{{host}} {{job_id}} {{user}}")],
            unit="Bps",
            legend="table",
            calcs=("lastNotNull", "max", "mean"),
            description=(
                "Each job's share of its node's NFS reads and writes (job:nfs_*_bytes:rate5m): the node's "
                "wire bytes split by its jobs' system-call I/O (roles/io_usage), so an estimate."
            ),
            links=[*job_links("${__field.labels.job_id}"), *host_links("${__field.labels.host}")],
        ),
        timeseries(
            "Top logins by disk I/O",
            [query(f"topk(10, sum by (host, user, device) ({LOGIN_IO}))", "{{host}} {{user}} {{device}}")],
            unit="Bps",
            legend="table",
            calcs=("lastNotNull", "max", "mean"),
            description="Each login's block I/O on the node's own disks, outside jobs (io.stat of its user slice).",
        ),
        row("Shared disks"),
        timeseries(
            "iocost",
            [query(SHARED, "{{host}} {{device}}")],
            max=1,
            decimals=0,
            w=8,
            description="1 while iocost shares the disk by cgroup weight (roles/io_fairness).",
        ),
        timeseries(
            "Disk busy",
            [
                query(
                    f"rate(node_disk_io_time_seconds_total[5m]) and on (host, device) {SHARED}",
                    "{{host}} {{device}}",
                )
            ],  # fmt: skip
            unit="percentunit",
            max=1,
            w=8,
            description="The share of time the disks under iocost had I/O in flight.",
        ),
        timeseries(
            "Disk latency",
            [
                query(
                    "rate(node_disk_read_time_seconds_total[5m]) / rate(node_disk_reads_completed_total[5m])"
                    f" and on (host, device) {SHARED}",
                    "read {{host}} {{device}}",
                ),
                query(
                    "rate(node_disk_write_time_seconds_total[5m]) / rate(node_disk_writes_completed_total[5m])"
                    f" and on (host, device) {SHARED}",
                    "write {{host}} {{device}}",
                ),
            ],
            unit="s",
            w=8,
            description="The mean time a read or a write took on the disks under iocost.",
        ),
        timeseries(
            "I/O waits by slice",
            [query('rate(io_usage_slice_pressure_seconds_total{kind="some"}[5m])', "{{host}} {{slice}}")],
            unit="percentunit",
            legend="table",
            calcs=("lastNotNull", "max", "mean"),
            w=24,
            description=(
                "The share of time some task of the slice waited for I/O (io.pressure): services "
                "(system.slice) should wait less than logins (user.slice) once iocost is on."
            ),
        ),
    ]
    return dashboard(
        "storage-io",
        "Storage / NFS and disks",
        panels,
        tags=["storage"],
        description="Who drives the NFS exports and the server's disks, by node, job and login, before and after iocost.",
    )
