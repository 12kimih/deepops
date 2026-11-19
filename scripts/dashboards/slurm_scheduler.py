"""Slurm / Scheduler: nodes, CPUs, GPUs, jobs and scheduler health of one Slurm cluster.

From slurmctld's own metrics (MetricsType=metrics/openmetrics; Prometheus scrapes its
/metrics/<set> endpoints, prometheus_slurmctld_metrics in roles/prometheus). Every name
read here is one the pinned Slurm serves (tests/cluster-tools/slurm-names.json, which
the tests check this dashboard against).
"""

from grafana import STATE_COLORS, dashboard, query, row, stat, timeseries

PATH = "src/dashboards/slurm-scheduler.json"

# Base states, exclusive: each node is in exactly one, so stacked they make every node.
NODE_STATES = {
    "alloc": "allocated",
    "mixed": "mixed",
    "idle": "idle",
    "down": "down",
}
# Flags beside the base state (an idle node can be drained): each its own series.
NODE_FLAGS = {
    "drained": "drained",
    "draining": "draining",
    "maint": "maintenance",
    "noresp": "not responding",
    "fail": "failed",
    "powered_down": "powered down",
}
# Ended jobs slurmctld still holds (for MinJobAge, 300 s by default), by outcome.
OUTCOMES = {
    "completed": ("Completed", "green"),
    "failed": ("Failed", "red"),
    "timeout": ("Timed out", "orange"),
    "cancelled": ("Cancelled", "yellow"),
    "preempted": ("Preempted", "purple"),
    "node_failed": ("Node failure", "dark-red"),
}


def m(name):
    """One cluster-wide series, deduplicated: a scrape-target label change never doubles it."""
    return f"max(slurm_{name})"


def per_node(name):
    """A per-node series (label node) summed over the nodes."""
    return f"sum(slurm_node_{name})"


def nodes(*states):
    return " + ".join(m(f"nodes_{s}") for s in states)


def build():
    other = f"{m('nodes')} - ({nodes(*NODE_STATES)})"
    panels = [
        row("Nodes"),
        stat("Nodes", m("nodes"), description="Every node Slurm knows."),
        stat("In use", nodes("alloc", "mixed"), description="Nodes running jobs (allocated or mixed)."),
        stat("Idle", nodes("idle"), thresholds=[(None, "text")]),
        stat(
            "Drained",
            nodes("drained", "draining"),
            description="Drained or draining: no new jobs start there.",
            thresholds=[(None, "green"), (1, "orange")],
        ),
        stat("Down or failed", nodes("down", "fail"), thresholds=[(None, "green"), (1, "red")]),
        stat(
            "GPUs allocated",
            f"{per_node('gpus_alloc')} / {per_node('gpus')}",
            unit="percentunit",
            decimals=0,
        ),
        timeseries(
            "Nodes by state",
            [query(m(f"nodes_{k}"), v) for k, v in NODE_STATES.items()] + [query(other, "other")],
            stack=True,
            decimals=0,
            w=12,
            h=9,
            legend="table",
            calcs=("lastNotNull", "max"),
            colors=STATE_COLORS,
            description="Base states, stacked, so the top is every node (other: future, unknown, blocked).",
        ),
        timeseries(
            "Nodes closed or out",
            [query(m(f"nodes_{k}"), v) for k, v in NODE_FLAGS.items()],
            decimals=0,
            w=12,
            h=9,
            legend="table",
            calcs=("lastNotNull", "max"),
            colors={**STATE_COLORS, "not responding": "red", "powered down": "text"},
            description="Flags beside the base state; a node can carry several.",
        ),
        timeseries(
            "CPUs",
            [query(per_node("cpus_alloc"), "Allocated"), query(per_node("cpus_idle"), "Idle")],
            stack=True,
            decimals=0,
            w=12,
            h=9,
            legend="table",
            calcs=("lastNotNull", "max"),
            colors={"Allocated": "blue", "Idle": "green"},
            description="CPUs jobs can use (CoreSpec excluded), stacked. Slurm counts the CPUs of"
            " drained and down nodes as idle; the node panels show those.",
        ),
        timeseries(
            "GPUs",
            [
                query(per_node("gpus_alloc"), "Allocated"),
                query(f"{per_node('gpus')} - {per_node('gpus_alloc')}", "Not allocated"),
            ],
            stack=True,
            decimals=0,
            w=12,
            h=9,
            legend="table",
            calcs=("lastNotNull", "max"),
            colors={"Allocated": "blue", "Not allocated": "green"},
            description="Configured GPUs (gres/gpu), stacked. Not allocated includes those on drained and down nodes.",
        ),
        row("Jobs"),
        stat("Running", m("jobs_running")),
        stat("Pending", m("jobs_pending")),
        stat("Completing", m("jobs_completing")),
        stat("Suspended", m("jobs_suspended")),
        stat(
            "Failed (recent)",
            m("jobs_failed"),
            description=OUTCOME_HELP,
            thresholds=[(None, "text"), (1, "orange")],
        ),
        stat(
            "Node failures (recent)",
            m("jobs_node_failed"),
            description=OUTCOME_HELP,
            thresholds=[(None, "green"), (1, "red")],
        ),
        timeseries(
            "Running and pending jobs",
            [
                query(m("jobs_running"), "Running"),
                query(m("jobs_pending"), "Pending"),
                query(m("jobs_completing"), "Completing"),
            ],
            decimals=0,
            w=12,
            h=9,
            legend="table",
            calcs=("lastNotNull", "max", "mean"),
            colors={"Running": "blue", "Pending": "yellow", "Completing": "light-blue"},
        ),
        timeseries(
            "Recently ended jobs by outcome",
            [query(m(f"jobs_{k}"), name) for k, (name, _) in OUTCOMES.items()],
            decimals=0,
            w=12,
            h=9,
            legend="table",
            calcs=("lastNotNull", "max"),
            colors={name: color for name, color in OUTCOMES.values()},
            description=OUTCOME_HELP,
        ),
        row("Scheduler"),
        timeseries(
            "Main scheduler cycle",
            [query(m("schedule_cycle_last"), "Last"), query(m("sched_mean_cycle"), "Mean")],
            unit="µs",
            w=8,
            h=8,
            calcs=("lastNotNull", "max"),
            description="How long one pass of the main scheduler took (as sdiag reports it).",
        ),
        timeseries(
            "Backfill cycle",
            [query(m("bf_cycle_last"), "Last"), query(m("bf_mean_cycle"), "Mean")],
            unit="µs",
            w=8,
            h=8,
            calcs=("lastNotNull", "max"),
            description="How long one backfill pass took (as sdiag reports it).",
        ),
        timeseries(
            "Backfill depth",
            [query(m("bf_depth_mean"), "Jobs considered per cycle (mean)")],
            decimals=0,
            w=8,
            h=8,
            calcs=("lastNotNull", "max"),
            description="How far down the queue backfill looks; it should cover the pending jobs.",
        ),
        stat("Controller threads", m("server_thread_cnt"), w=8),
        stat(
            "Agent queue",
            m("agent_queue_size"),
            w=8,
            description="RPCs waiting for slurmctld's agent to retry; a growing queue means the controller lags.",
            thresholds=[(None, "green"), (50, "orange"), (200, "red")],
        ),
        stat(
            "Pending queue",
            m("schedule_queue_len"),
            w=8,
            description="Jobs the main scheduler had to consider on its last cycle.",
        ),
    ]
    return dashboard(
        "slurm-scheduler",
        "Slurm / Scheduler",
        panels,
        tags=["slurm"],
        description="Nodes, CPUs, GPUs, jobs and scheduler health of the Slurm cluster (slurmctld's own metrics).",
    )


OUTCOME_HELP = "Ended jobs slurmctld still holds (MinJobAge, 300 s by default), so a short-lived count."
