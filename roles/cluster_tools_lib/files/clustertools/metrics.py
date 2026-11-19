"""metrics: GPU allocation from Slurm as Prometheus metrics, for node_exporter.

Slurm's own exporters report jobs and nodes, not which GPU each job holds. This writes:
  slurm_gpu_allocated{node,gpu,gpu_type,user,account,partition,job_id} 1 per allocated GPU
  slurm_node_gpus{node,gpu_type}                                    GPUs configured
  slurm_node_state{node,state}         1 per node, Slurm's base state (idle, mixed, down...)
  slurm_node_drain{node}               1 when the drain flag is set beside that state
  slurm_node_schedulable{node}         1 when a new job can start there (vocab.node_schedulable,
                                       the definition sq's free capacity uses)
  slurm_metrics_updated_timestamp_seconds   when this data was read from Slurm
so a dashboard can show who holds which GPU, and an alert can compare DCGM utilisation
with allocation (a busy GPU no job holds). Label names avoid those the scrape attaches
itself, which would be renamed exported_*: `job` and `instance` (Prometheus's own) and
`host` (this site's target label). Hence `node`, as in Slurm's own metrics, and `job_id`.

`metrics PATH` writes the text file atomically (a temporary file in the same directory,
fsync, rename), which node_exporter's textfile collector needs so it never reads half a
file. On a Slurm failure it exits 1 and leaves the previous file, whose timestamp then
shows how stale it is. The alerts read that series, not the file's mtime, so they hold
whatever the file is called. Python 3.9 or newer, standard library only.
"""

import sys
import time

from . import slurm, vocab
from .cli import run_main
from .files import write_atomic
from .prom import series


def textfile(jobs_data, nodes_data, now=None):
    """The metrics for a `scontrol -d --json show jobs` and a `show nodes` reply, read
    at NOW (default: the current time)."""
    lines = [
        "# HELP slurm_metrics_updated_timestamp_seconds When this data was read from Slurm.",
        "# TYPE slurm_metrics_updated_timestamp_seconds gauge",
        series("slurm_metrics_updated_timestamp_seconds", {}, int(time.time() if now is None else now)),
        "# HELP slurm_gpu_allocated 1 for each GPU a job holds.",
        "# TYPE slurm_gpu_allocated gauge",
    ]
    for job in jobs_data["jobs"]:
        if not vocab.job_holds(job["job_state"]):
            continue
        gpus, types = slurm.job_gpus(job)
        for host, index in gpus:
            labels = {
                "node": host, "gpu": index, "gpu_type": types[(host, index)], "user": job["user_name"],
                "account": job.get("account") or "", "partition": job.get("partition") or "",
                "job_id": slurm.job_label(job),
            }  # fmt: skip
            lines.append(series("slurm_gpu_allocated", labels, 1))
    lines += ["# HELP slurm_node_gpus GPUs configured on a node, by type.", "# TYPE slurm_node_gpus gauge"]
    for node in nodes_data["nodes"]:
        for gtype, count in sorted(slurm.node_gpus(node.get("gres")).items()):
            lines.append(series("slurm_node_gpus", {"node": node["name"], "gpu_type": gtype}, count))
    # No reason text as a label: it is free text, so every new reason would be a new series.
    for name, help_text, value in (
        ("slurm_node_state", "Slurm's base state of a node, 1 for the current one.", None),
        ("slurm_node_drain", "1 when the node's drain flag is set.", vocab.node_drain),
        ("slurm_node_schedulable", "1 when a new job can start on the node.", vocab.node_schedulable),
    ):
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
        for node in nodes_data["nodes"]:
            if value is None:
                lines.append(series(name, {"node": node["name"], "state": vocab.node_base(node["state"])}, 1))
            else:
                lines.append(series(name, {"node": node["name"]}, int(value(node["state"]))))
    return "\n".join(lines) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0].startswith("-"):
        print("usage: metrics PATH  (a node_exporter textfile, e.g. .../slurm-gpu.prom)", file=sys.stderr)
        return 2
    return run_main(
        "metrics", lambda: write_atomic(argv[0], textfile(slurm.load("jobs_detail"), slurm.load("nodes")))
    )


if __name__ == "__main__":
    sys.exit(main())
