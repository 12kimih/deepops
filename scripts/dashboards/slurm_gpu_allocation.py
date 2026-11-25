"""Slurm / GPU allocation: who holds which GPU, against what the cluster can offer.

From roles/slurm_metrics (cluster_tools.metrics: slurm_gpu_allocated, slurm_gpu_configured,
slurm_node_state, slurm_node_drain, slurm_node_schedulable) and the recording rule
host_device:slurm_gpu_allocated:max (rules/slurm.yml), joined with DCGM utilisation
through host_device:dcgm_gpu_util_ratio:max, which carries Slurm's GPU numbering, and
per job through job_device:dcgm_gpu_util_ratio:max and job_device:dcgm_fb_used_bytes:max.
"""

from grafana import (
    GPU_ALLOCATION,
    STATE_COLORS,
    bars,
    dashboard,
    host_links,
    job_links,
    label_variable,
    query,
    stat,
    table,
    timeseries,
)

PATH = "src/dashboards/slurm-gpu-allocation.json"

ALLOC = "host_device:slurm_gpu_allocated:max"
NODE_GPUS = "max by (node, gpu_type) (slurm_gpu_configured)"
SCHEDULABLE = "max by (node) (slurm_node_schedulable)"
USABLE = f"{NODE_GPUS} * on (node) group_left () {SCHEDULABLE}"
ALLOCATED = f"(count({ALLOC}) or vector(0))"
JOB_UTIL = "job_device:dcgm_gpu_util_ratio:max"
JOB_MEMORY = "job_device:dcgm_fb_used_bytes:max"
JOB = "job_id, user, account"
# The user and job variables: the panels about jobs show the chosen ones (All by
# default); the cluster's capacity and totals do not.
MINE = 'user=~"$user", job_id=~"$job_id"'
MY_ALLOC = f"{ALLOC}{{{MINE}}}"
NONE_ALLOCATED = "No GPU allocated"


def share(title, by, w):
    """Allocated GPUs over the group's GPUs; groups with nothing allocated show 0."""
    expr = (
        f"(sum by ({by}) (count by ({by}, host, device) ({ALLOC})) or 0 * sum by ({by}) ({NODE_GPUS}))"
        f" / sum by ({by}) ({NODE_GPUS})"
    )
    return bars(title, expr, by, w=w, h=8)


def node_table():
    gpus = f"{NODE_GPUS} * on (node) group_left (state) max by (node, state) (slurm_node_state)"
    drained = "max by (node) (slurm_node_drain) == 1"
    expr = (
        f'label_replace(({gpus}) and on (node) ({drained}), "state", "$1, drain", "state", "(.+)")'
        f" or (({gpus}) unless on (node) ({drained}))"
    )
    return table(
        "Nodes",
        expr,
        ["node", "gpu_type", "state", "GPUs"],
        w=10,
        h=10,
        sort="node",
        value=("GPUs", "none", None),
        links={"node": host_links("${__value.raw}")},
        colors={
            "state": {
                "down|fail|error": STATE_COLORS["down"],
                "drain": STATE_COLORS["drained"],
                "^(idle|mixed|allocated)$": STATE_COLORS["idle"],
            }
        },
        description="Each node's GPUs and Slurm state; no new job starts on a drained or down node.",
    )


def build():
    panels = [
        stat("GPUs", f"sum({NODE_GPUS})", description="GPUs Slurm has configured (gres)."),
        stat(
            "Usable",
            f"sum({USABLE})",
            description="GPUs on nodes where new jobs can start (not drained or down).",
            thresholds=[(None, "red"), (1, "text")],
        ),
        stat("Allocated", ALLOCATED, description="GPUs held by running jobs."),
        stat(
            "Allocated of usable",
            f"{ALLOCATED} / sum({USABLE})",
            unit="percentunit",
            decimals=0,
            no_value="No usable GPU",
        ),
        stat("Users", f"count(count by (user) ({ALLOC})) or vector(0)", description="Users holding GPUs."),
        stat(
            "Busy without a job",
            'count(ALERTS{alertname="GpuBusyWithoutJob"}) or vector(0)',
            description="GPUs in use that no Slurm job holds (alert GpuBusyWithoutJob, pending or firing).",
            thresholds=[(None, "green"), (1, "red")],
        ),
        timeseries(
            "Capacity and allocation",
            [
                query(f"sum({NODE_GPUS})", "Configured"),
                query(f"sum({USABLE})", "Usable"),
                query(ALLOCATED, "Allocated"),
            ],
            decimals=0,
            w=14,
            h=10,
            legend="table",
            calcs=("lastNotNull", "min", "max"),
            fill=0,
            colors={"Configured": "text", "Usable": "green", "Allocated": "blue"},
            description="Usable drops when nodes are drained or down; allocation can only use what is usable.",
        ),
        node_table(),
        timeseries(
            "Allocated by user",
            [query(f"count by (user) ({MY_ALLOC})", "{{user}}")],
            stack=True,
            decimals=0,
            w=12,
            h=9,
            legend="table",
            calcs=("lastNotNull", "max"),
            no_value=NONE_ALLOCATED,
        ),
        timeseries(
            "Allocated by account",
            [query(f"count by (account) ({ALLOC})", "{{account}}")],
            stack=True,
            decimals=0,
            w=12,
            h=9,
            legend="table",
            calcs=("lastNotNull", "max"),
            no_value=NONE_ALLOCATED,
        ),
        share("Share allocated by GPU type", "gpu_type", 8),
        share("Share allocated by node", "node", 16),
        table(
            "Allocations now",
            # Every allocation, with its utilisation where DCGM reports one (NaN otherwise).
            f"{MY_ALLOC} * on (host, device) group_left () host_device:dcgm_gpu_util_ratio:max or {MY_ALLOC} * (0 / 0)",
            ["user", "account", "partition", "job_id", "node", "gpu", "gpu_type", "Utilization"],
            h=10,
            sort="node",
            value=("Utilization", "percentunit", "continuous-RdYlGr"),
            no_value=NONE_ALLOCATED,
            links={"job_id": job_links("${__value.raw}"), "node": host_links("${__value.raw}")},
            description="Each GPU a running job holds, with its current utilisation (DCGM). "
            "A job opens it alone here, a node its GPU and host dashboards.",
        ),
        timeseries(
            "GPU utilization by job",
            [query(f"avg by ({JOB}) ({JOB_UTIL}{{{MINE}}})", "{{job_id}} {{user}}")],
            unit="percentunit",
            max=1,
            decimals=0,
            w=12,
            h=10,
            legend="table",
            calcs=("mean", "max", "lastNotNull"),
            fill=0,
            no_value=NONE_ALLOCATED,
            links=job_links("${__field.labels.job_id}"),
            description="Each job's mean utilisation over the GPUs it holds (DCGM, Slurm's GPU numbering); "
            "the legend's mean and max cover the time range.",
        ),
        timeseries(
            "GPU memory by job",
            [query(f"sum by ({JOB}) ({JOB_MEMORY}{{{MINE}}})", "{{job_id}} {{user}}")],
            unit="bytes",
            w=12,
            h=10,
            legend="table",
            calcs=("mean", "max", "lastNotNull"),
            fill=0,
            no_value=NONE_ALLOCATED,
            links=job_links("${__field.labels.job_id}"),
            description="GPU memory each job uses, summed over the GPUs it holds (DCGM framebuffer used).",
        ),
        table(
            "GPUs busy without a job",
            'ALERTS{alertname="GpuBusyWithoutJob"}',
            ["host", "gpu", "alertstate"],
            h=6,
            sort="host",
            no_value="None",
            links={"host": host_links("${__value.raw}")},
            description="From the GpuBusyWithoutJob alert: pending for the first 15 minutes, then firing.",
        ),
    ]
    variables = [
        label_variable("user", ALLOC, "user", label_text="User"),
        label_variable("job_id", f'{ALLOC}{{user=~"$user"}}', "job_id", label_text="Job"),
    ]
    return dashboard(
        GPU_ALLOCATION,
        "Slurm / GPU allocation",
        panels,
        tags=["slurm", "gpu"],
        variables=variables,
        description="Who holds which GPU: Slurm allocation (roles/slurm_metrics) against usable capacity, "
        "joined with DCGM utilisation; each job's GPU use, with User and Job to pick your own.",
    )
