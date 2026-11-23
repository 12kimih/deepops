"""gpu-usage: what a job's GPUs did, running or finished: utilisation and memory, and
how much of its CPUs it kept busy.

Two sources, each row naming its own:
  slurm       Slurm's accounting (sacct TRESUsageIn gres/gpuutil, gres/gpumem), per job
              step: each task's high-water mark, averaged (mean) and maxed (max) over the
              step's tasks. Slurm records them only with AutoDetect=nvml in gres.conf.
  prometheus  DCGM through the monitoring stack (rules/slurm.yml job_device:*), per GPU:
              mean and max over the job's run of utilisation, SM activity (how much of
              the GPU the kernels kept busy), memory and board power. Kept for Prometheus' retention; a job
              shorter than the allocation interval (a minute) may have no samples.
CPU: Slurm's own accounting for every job, its CPU time (TotalCPU, user plus system,
summed over the steps) against its allocated CPUs over its elapsed time, as seff
computes it; Slurm adds a step's CPU time when the step ends.
--source auto (the default) shows Slurm's figures where the job has them, else
Prometheus'. The job comes from sacct, so finished jobs work as well as running ones,
and from every cluster the accounting database holds: a job of the cluster's old name
(a rename keeps that history apart) is found too, and each report names its cluster.
Python 3.9 or newer, standard library only.
"""

import json
import sys
import time

from . import slurm
from .cli import Command, Opt, opt_json, run_main
from .prometheus import Error as PrometheusError
from .prometheus import Prometheus
from .render import Grid, duration
from .site import PATH as SITE_PATH
from .site import info as site_info

SOURCES = ("auto", "slurm", "prometheus")
# Prometheus' series (rules/slurm.yml), labelled with the job as Slurm names it.
# Prometheus' per-job series (rules/slurm.yml), labelled with the job as Slurm names it:
# (figure, series, unit suffix of its keys). Utilisation says a kernel ran; SM activity
# how much of the GPU it kept busy; memory and board power what it held and drew.
SERIES = (
    ("util", "job_device:dcgm_gpu_util_ratio:max", ""),
    ("sm", "job_device:dcgm_sm_util_ratio:max", ""),
    ("memory", "job_device:dcgm_fb_used_bytes:max", "_bytes"),
    ("power", "job_device:dcgm_power_watts:max", "_watts"),
)
STATS = (("mean", "avg_over_time"), ("max", "max_over_time"))
FIGURES = tuple(f"{name}_{stat}{unit}" for name, _, unit in SERIES for stat, _ in STATS)
STEP = 60  # the allocation interval (roles/slurm_metrics): the shortest window with a sample

NOTES = [
    "Sources (each row names its own):\n"
    "  slurm       sacct's TRESUsageInAve and TRESUsageInMax for gres/gpuutil and gres/gpumem, per\n"
    "              job step: each task's high-water mark, averaged and maxed over the tasks.\n"
    "              Present only where Slurm gathers GPU usage (AutoDetect=nvml in gres.conf).\n"
    "  prometheus  DCGM, per GPU in Slurm's numbering: the mean and the max over the job's run,\n"
    "              with SM activity (how much of the GPU the kernels kept busy: utilisation only\n"
    "              says a kernel ran) and board power, which Slurm does not record.\n"
    "              Kept as long as Prometheus keeps data; a job under a minute may have none.\n"
    "--source auto (the default) prefers Slurm's figures and falls back to Prometheus'.",
    "CPU: the job's CPU time (sacct TotalCPU) over its allocated CPUs times its elapsed\n"
    "time, as seff computes it: what share of its CPUs it kept busy. Slurm adds a step's\n"
    "CPU time when the step ends, so a running job's share counts its finished steps.",
]

COMMAND = Command(
    "gpu-usage",
    "what a job's GPUs did: utilisation, SM activity, memory and power, running or finished",
    [
        Opt("job", metavar="JOB", brief=True, help="the job: an id, or ARRAY_TASK for an array task"),
        Opt(
            "--source",
            metavar="WHERE",
            default="auto",
            choices=SOURCES,
            brief=True,
            help="auto (default: Slurm's figures where the job has them, else Prometheus'), slurm or prometheus",
        ),
        opt_json("the figures as JSON, for scripts"),
    ],
    usage="gpu-usage [OPTIONS] JOB",
    machine="--json",
    notes=NOTES,
)


def holds_gpus(job):
    return any(
        k == "gres/gpu" or k.startswith("gres/gpu:") for k in slurm.tres_list(job["tres"]["allocated"])
    )


def gpu_count(job):
    return slurm.tres_list(job["tres"]["allocated"]).get("gres/gpu") or 0


def cpu_figures(job):
    """(CPUs allocated, CPU seconds used, share of the allocation kept busy); the share
    is None for a job that has not run a second."""
    cpus = slurm.tres_list(job["tres"]["allocated"]).get("cpu") or 0
    used = slurm.seconds(job["time"].get("total"))
    elapsed = job["time"].get("elapsed") or 0
    return cpus, used, (used / (cpus * elapsed) if cpus and elapsed else None)


def window(job, now):
    """(start, end) of the job's run in Unix time; end is NOW while it runs, and None
    for a job that never started."""
    start, end = job["time"].get("start") or 0, job["time"].get("end") or 0
    if not start:
        return None
    return start, (end if end and end >= start else now)


def slurm_rows(job):
    """One row per step that has Slurm's GPU usage figures."""
    rows = []
    for step in job.get("steps") or []:
        peak, mean = slurm.step_usage(step, "max"), slurm.step_usage(step, "average")
        if "gres/gpuutil" not in peak and "gres/gpumem" not in peak:
            continue
        rows.append(
            {
                "source": "slurm",
                "where": (step.get("step") or {}).get("id", ""),
                **dict.fromkeys(FIGURES),  # Slurm records no SM activity or power
                "util_mean": _ratio(mean.get("gres/gpuutil")),
                "util_max": _ratio(peak.get("gres/gpuutil")),
                "memory_mean_bytes": mean.get("gres/gpumem"),
                "memory_max_bytes": peak.get("gres/gpumem"),
            }
        )
    return rows


def _ratio(percent):
    return None if percent is None else percent / 100


def prometheus_rows(api, label, start, end):
    """One row per GPU the job held, from Prometheus over [START, END]."""
    span = max(int(end - start), STEP)
    selector = f'{{job_id="{label}"}}'
    figures = {}
    for name, series, unit in SERIES:
        for stat, function in STATS:
            key = f"{name}_{stat}{unit}"
            for r in api.query(f"max by (host, device) ({function}({series}{selector}[{span}s]))", at=end):
                where = f"{r['metric'].get('host', '?')}/{r['metric'].get('device', '?')}"
                figures.setdefault(where, {})[key] = float(r["value"][1])
    return [
        {"source": "prometheus", "where": w, **{k: f.get(k) for k in FIGURES}}
        for w, f in sorted(figures.items())
    ]


def report(job, source, api, now):
    """The figures of one job record: its identity, rows and notes on what is missing."""
    label = slurm.accounting_label(job)
    cpus, cpu_seconds, cpu_efficiency = cpu_figures(job)
    out = {
        "job": label,
        "cluster": job["cluster"],
        "user": job["user"],
        "account": job["account"],
        "partition": job["partition"],
        "state": "/".join(job["state"]["current"]),
        "nodes": job["nodes"],
        "gpus": gpu_count(job),
        "elapsed": job["time"].get("elapsed") or 0,
        "cpus": cpus,
        "cpu_seconds": cpu_seconds,
        "cpu_efficiency": cpu_efficiency,
        "rows": [],
        "notes": [],
    }
    if source in ("auto", "slurm"):
        out["rows"] = slurm_rows(job)
        if not out["rows"]:
            out["notes"].append("Slurm recorded no GPU usage (it gathers it only with AutoDetect=nvml)")
    if source == "prometheus" or (source == "auto" and not out["rows"]):
        span = window(job, now)
        if span is None:
            out["notes"].append("the job has not started")
        elif api is None:
            out["notes"].append(f"no prometheus_url in {SITE_PATH}")
        else:
            try:
                rows = prometheus_rows(api, label, *span)
            except PrometheusError as e:
                out["notes"].append(str(e))
            else:
                out["rows"] += rows
                if not rows:
                    out["notes"].append(
                        "Prometheus has no samples of it (older than its retention, under a minute, "
                        "or from before the per-job rules were deployed)"
                    )
    return out


def _percent(ratio):
    return "-" if ratio is None else f"{100 * ratio:.0f}%"


def _gib(count):
    return "-" if count is None else f"{count / 2**30:.1f}G"


def _watts(power):
    return "-" if power is None else f"{power:.0f}W"


# The table's columns after GPU / STEP: (heading, figure, format).
COLUMNS = (
    ("UTIL MEAN", "util_mean", _percent),
    ("UTIL MAX", "util_max", _percent),
    ("SM MEAN", "sm_mean", _percent),
    ("SM MAX", "sm_max", _percent),
    ("MEM MEAN", "memory_mean_bytes", _gib),
    ("MEM MAX", "memory_max_bytes", _gib),
    ("POWER MEAN", "power_mean_watts", _watts),
    ("POWER MAX", "power_max_watts", _watts),
)


def cpu_line(r):
    if r["cpu_efficiency"] is None:
        return f"CPU: {r['cpus']} allocated, no run time to measure against"
    busy = r["cpu_seconds"] / r["elapsed"]
    line = f"CPU: {busy:.1f} of {r['cpus']} busy on average ({_percent(r['cpu_efficiency'])})"
    # Slurm adds a step's CPU time when it ends: a running step is not counted yet.
    return line + (", finished steps only" if "RUNNING" in r["state"].split("/") else "")


def render(reports, theme):
    out = []
    for r in reports:
        grid = Grid(theme, ["GPU / STEP", *(c[0] for c in COLUMNS), "SOURCE"],
                    align=["l", *("r" for _ in COLUMNS), "l"])  # fmt: skip
        for row in r["rows"]:
            grid.row(
                [
                    (row["where"], "text"),
                    *((fmt(row[key]), "text") for _, key, fmt in COLUMNS),
                    (row["source"], "muted"),
                ]
            )
        tally = [
            (f"{r['user']} / {r['account']}", "sub"),
            (r["state"], "sub"),
            (
                f"{duration(r['elapsed'])} on {r['nodes']}, {r['gpus']} GPU{'s' if r['gpus'] != 1 else ''}",
                "sub",
            ),
        ]
        title = f"job {r['job']} on {r['cluster']}"
        out.append(grid.render(title, tally) if r["rows"] else theme.section(title, theme.cols, tally))
        out.append(f"  {theme.paint('sub', cpu_line(r))}")
        out += [f"  {theme.paint('muted', note)}" for note in r["notes"]]
    return "\n".join(out) + "\n"


def main(argv=None):
    args, theme = COMMAND.parse(argv)

    def body():
        jobs = slurm.load("accounting", args.job)["jobs"]
        if not jobs:
            print(f"gpu-usage: no job {args.job} in Slurm's accounting", file=sys.stderr)
            return 1
        held = [j for j in jobs if holds_gpus(j)]
        if not held:
            print(f"gpu-usage: job {args.job} holds no GPUs", file=sys.stderr)
            return 1
        url = site_info().get("prometheus_url")
        api = Prometheus(url) if url else None
        now = time.time()
        reports = [report(j, args.source, api, now) for j in held]
        if args.json:
            print(json.dumps(reports, indent=2))
        else:
            sys.stdout.write(render(reports, theme))
        return 0

    return run_main("gpu-usage", body)


if __name__ == "__main__":
    sys.exit(main())
