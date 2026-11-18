"""Slurm / Scheduler: nodes, CPUs, jobs and scheduler health of one Slurm cluster.

From roles/prometheus-slurm-exporter (sinfo, squeue and sdiag counts, one series per
metric). With Slurm 26.05 its native OpenMetrics endpoints replace the exporter; only
the queries below change then.
"""

from grafana import STATE_COLORS, dashboard, query, row, stat, timeseries

PATH = "roles/prometheus-slurm-exporter/files/slurm-scheduler.json"

# Exporter suffix -> state name shown (STATE_COLORS keys). The exporter's states are
# exclusive, so they add up to every node.
NODE_STATES = {
    "alloc": "allocated",
    "mix": "mixed",
    "idle": "idle",
    "comp": "completing",
    "drain": "drained",
    "maint": "maintenance",
    "down": "down",
    "fail": "failed",
    "err": "error",
}
# Ended jobs squeue still lists (for MinJobAge, 300 s by default), by outcome.
OUTCOMES = {
    "completed": ("Completed", "green"),
    "failed": ("Failed", "red"),
    "timeout": ("Timed out", "orange"),
    "cancelled": ("Cancelled", "yellow"),
    "preempted": ("Preempted", "purple"),
    "node_fail": ("Node failure", "dark-red"),
}


def m(name):
    """One exporter series, deduplicated: a scrape-target label change never doubles it."""
    return f"max(slurm_{name})"


def nodes(*suffixes):
    return " + ".join(m(f"nodes_{s}") for s in suffixes)


def build():
    all_states = tuple(NODE_STATES)
    panels = [
        row("Nodes"),
        stat("Nodes", nodes(*all_states), description="Every node Slurm knows."),
        stat("In use", nodes("alloc", "mix"), description="Nodes running jobs (allocated or mixed)."),
        stat("Idle", nodes("idle"), thresholds=[(None, "text")]),
        stat(
            "Drained",
            nodes("drain"),
            description="Drained or draining: no new jobs start there.",
            thresholds=[(None, "green"), (1, "orange")],
        ),
        stat("Down or failed", nodes("down", "fail", "err"), thresholds=[(None, "green"), (1, "red")]),
        stat("CPUs allocated", f"{m('cpus_alloc')} / {m('cpus_total')}", unit="percentunit", decimals=0),
        timeseries(
            "Nodes by state",
            [query(m(f"nodes_{k}"), v) for k, v in NODE_STATES.items()],
            stack=True,
            decimals=0,
            w=12,
            h=9,
            legend="table",
            calcs=("lastNotNull", "max"),
            colors=STATE_COLORS,
            description="Stacked, so the top is every node.",
        ),
        timeseries(
            "CPUs",
            [
                query(m("cpus_alloc"), "Allocated"),
                query(m("cpus_idle"), "Idle"),
                query(m("cpus_other"), "Other (drained, down)"),
            ],
            stack=True,
            decimals=0,
            w=12,
            h=9,
            legend="table",
            calcs=("lastNotNull", "max"),
            colors={"Allocated": "blue", "Idle": "green", "Other (drained, down)": "orange"},
            description="Stacked, so the top is every CPU.",
        ),
        row("Jobs"),
        stat("Running", m("queue_running")),
        stat("Pending", m("queue_pending")),
        stat("Completing", m("queue_completing")),
        stat("Suspended", m("queue_suspended")),
        stat(
            "Failed (recent)",
            m("queue_failed"),
            description=OUTCOME_HELP,
            thresholds=[(None, "text"), (1, "orange")],
        ),
        stat(
            "Node failures (recent)",
            m("queue_node_fail"),
            description=OUTCOME_HELP,
            thresholds=[(None, "green"), (1, "red")],
        ),
        timeseries(
            "Running and pending jobs",
            [
                query(m("queue_running"), "Running"),
                query(m("queue_pending"), "Pending"),
                query(m("queue_completing"), "Completing"),
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
            [query(m(f"queue_{k}"), name) for k, (name, _) in OUTCOMES.items()],
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
            [query(m("scheduler_last_cycle"), "Last"), query(m("scheduler_mean_cycle"), "Mean")],
            unit="µs",
            w=8,
            h=8,
            calcs=("lastNotNull", "max"),
            description="How long one pass of the main scheduler took (sdiag).",
        ),
        timeseries(
            "Backfill cycle",
            [
                query(m("scheduler_backfill_last_cycle"), "Last"),
                query(m("scheduler_backfill_mean_cycle"), "Mean"),
            ],
            unit="µs",
            w=8,
            h=8,
            calcs=("lastNotNull", "max"),
            description="How long one backfill pass took (sdiag).",
        ),
        timeseries(
            "Backfill depth",
            [query(m("scheduler_backfill_depth_mean"), "Jobs considered per cycle (mean)")],
            decimals=0,
            w=8,
            h=8,
            calcs=("lastNotNull", "max"),
            description="How far down the queue backfill looks; it should cover the pending jobs.",
        ),
        stat("Scheduler threads", m("scheduler_threads"), w=8),
        stat(
            "Agent queue",
            m("scheduler_queue_size"),
            w=8,
            description="RPCs waiting for slurmctld's agent; a growing queue means the controller lags.",
            thresholds=[(None, "green"), (50, "orange"), (200, "red")],
        ),
        stat(
            "Exporter errors",
            m("exporter_collector_errors"),
            w=8,
            description="Collectors that failed on the last scrape; their series are missing meanwhile.",
            thresholds=[(None, "green"), (1, "red")],
        ),
    ]
    return dashboard(
        "slurm-scheduler",
        "Slurm / Scheduler",
        panels,
        tags=["slurm"],
        description="Nodes, CPUs, jobs and scheduler health of the Slurm cluster.",
    )


OUTCOME_HELP = "Ended jobs squeue still lists (MinJobAge, 300 s by default), so a short-lived count."
