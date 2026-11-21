#!/usr/bin/env python3
"""Make test fixtures for the cluster tools from a real cluster's Slurm JSON.

Capture on a Slurm client (read-only):
    scontrol --json show nodes > nodes.json;  scontrol --json show partitions > partitions.json
    scontrol -d --json show jobs > jobs.json; sacctmgr --json show qos > qos.json
    sacctmgr --json show assoc > assoc.json
    sacct --json --clusters=all -a -S now-3days -E now > accounting.json
then run:  make_fixtures.py CAPTURE_DIR fixtures/slurm-<release>
Only the files the capture holds are written, so a part can be refreshed alone.

Only the fields the tools read are kept, and every site-specific name is replaced by a
generic one derived from the data itself (GPU types by order, users, accounts, QoS team
suffixes, the cluster name), so the output carries no site data and this script needs
no list of the real names. Review the output anyway before committing it.
"""

import json
import re
import sys
from pathlib import Path

GPU_NAMES = ["a10", "a30", "a40", "h100", "h200", "b200", "l4", "t4"]
KEEP_WORDS = {"root", "normal", "low", "high", "hotfix", "debug", "maintenance", ""}

NODE_FIELDS = (
    "name",
    "state",
    "partitions",
    "cpus",
    "alloc_cpus",
    "effective_cpus",
    "real_memory",
    "alloc_memory",
    "specialized_memory",
    "gres",
    "gres_used",
    "tres",
    "tres_used",
    "reason",
    "reason_set_by_user",
    "reason_changed_at",
)
JOB_FIELDS = (
    "job_id",
    "name",
    "user_name",
    "account",
    "partition",
    "qos",
    "job_state",
    "state_reason",
    "state_description",
    "nodes",
    "node_count",
    "cpus",
    "tres_req_str",
    "tres_alloc_str",
    "tres_per_node",
    "array_job_id",
    "array_task_id",
    "array_task_string",
    "gres_detail",
    "time_limit",
    "start_time",
    "submit_time",
    "priority",
)
QOS_FIELDS = ("name", "priority", "preempt", "flags", "limits")
ASSOC_FIELDS = ("cluster", "account", "user", "qos", "default", "is_default", "partition")
ACCOUNTING_FIELDS = (
    "cluster",
    "job_id",
    "user",
    "account",
    "partition",
    "nodes",
    "state",
    "time",
    "array",
    "steps",
    "tres",
)
STEP_FIELDS = ("step", "state")
ACCOUNTING_KEPT = 12  # each record carries its steps' TRES lists
STEPS_KEPT = 4  # the first of a job's steps: a job of many srun steps would swell the file
STEP_NAMES = {"batch", "extern", "interactive"}  # Slurm's own; others are commands
JOBS_KEPT = 40
FILES = ("nodes", "partitions", "jobs", "qos", "assoc", "accounting")


def _whole(name):
    """A pattern for NAME as a whole name; a "_suffix" only where it ends a name."""
    if name.startswith("_"):
        return re.escape(name) + r"(?![A-Za-z0-9_])"
    return rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"


def pick(d, fields):
    return {k: d[k] for k in fields if k in d}


def accounting_jobs(jobs):
    """Finished jobs that used something, one per cluster, state and array kind first
    (an array task among them), each with only the fields the tools read (TRESUsageIn
    max and average of each step, the allocation, the run's times); command step names
    become step<N>."""
    used = [j for j in jobs if any(s["tres"]["requested"]["max"] for s in j["steps"])]
    seen, kept = set(), []
    for j in used:
        kind = (j["cluster"], tuple(j["state"]["current"]), bool(j["array"]["job_id"]))
        if kind not in seen:
            seen.add(kind)
            kept.append(j)
    kept += [j for j in used if j not in kept][: max(0, ACCOUNTING_KEPT - len(kept))]
    out = []
    for j in kept:
        job = pick(j, ACCOUNTING_FIELDS)
        job["steps"] = []
        for i, s in enumerate(j["steps"][:STEPS_KEPT]):
            step = pick(s, STEP_FIELDS)
            name = s["step"]["name"]
            step["step"] = {"id": s["step"]["id"], "name": name if name in STEP_NAMES else f"step{i}"}
            step["tres"] = {"requested": pick(s["tres"]["requested"], ("max", "average"))}
            job["steps"].append(step)
        job["array"] = pick(j["array"], ("job_id", "task_id", "task"))
        job["time"] = pick(j["time"], ("start", "end", "elapsed"))
        job["state"] = pick(j["state"], ("current",))
        job["tres"] = pick(j["tres"], ("allocated",))
        out.append(job)
    return out


def main(src, dst):
    src, dst = Path(src), Path(dst)
    raw = {f: json.loads((src / f"{f}.json").read_text()) for f in FILES if (src / f"{f}.json").exists()}
    if not raw:
        sys.exit(f"{src}: none of {', '.join(f + '.json' for f in FILES)}")
    empty = {"nodes": [], "jobs": [], "qos": [], "associations": []}
    nodes = raw.get("nodes", empty)["nodes"]
    jobs = raw.get("jobs", empty)["jobs"]
    assoc = raw.get("assoc", empty)["associations"]
    accounting = raw.get("accounting", empty)["jobs"]

    # Names to replace, discovered from the data.
    names = {}
    types = sorted(
        {t for n in nodes for t in re.findall(r"gpu:([^:,(]+):", n.get("gres") or "")}
        | {
            t["name"][4:]
            for j in accounting
            for t in j["tres"]["allocated"]
            if t.get("name", "").startswith("gpu:")
        }
    )
    for real, generic in zip(types, GPU_NAMES):
        names[real] = generic
    users = sorted(
        {j["user_name"] for j in jobs}
        | {j["user"] for j in accounting}
        | {a["user"] for a in assoc if a.get("user")}
        | {n.get("reason_set_by_user") for n in nodes} - {None, ""}
    )
    for i, u in enumerate(u for u in users if u not in KEEP_WORDS):
        names[u] = f"user{i + 1}"
    accounts = sorted({a["account"] for a in assoc} | {j["account"] for j in accounting})
    for i, a in enumerate(a for a in accounts if a not in KEEP_WORDS and a not in names):
        names[a] = f"acct{i + 1}"
    qos = raw.get("qos", empty)["qos"]
    suffixes = sorted({q["name"].split("_", 1)[1] for q in qos if "_" in q["name"]})
    # The reply's own cluster (its meta) is testcluster; any other the database holds
    # (a renamed cluster's history keeps the old name) is cluster<N>.
    here = {d["meta"]["slurm"]["cluster"] for d in raw.values()}
    others = {j["cluster"] for j in accounting} | {a["cluster"] for a in assoc}
    for i, c in enumerate(sorted(others - here)):
        names[c] = f"cluster{i + 1}"
    names.update({c: "testcluster" for c in here})
    for i, s in enumerate(suffixes):
        names[f"_{s}"] = f"_t{i + 1}"  # numbered: a letter could equal a real suffix
    for q in qos:
        if "_" not in q["name"] and q["name"] not in KEEP_WORDS:
            names[q["name"]] = f"qos{len(names)}"

    seen, kept = set(), []
    for j in jobs:  # one job per kind first, then fill up
        kind = (tuple(j["job_state"]), j.get("state_reason"), j.get("qos"), bool(j.get("array_task_string")))
        if kind not in seen:
            seen.add(kind)
            kept.append(j)
    kept += [j for j in jobs if j not in kept][: max(0, JOBS_KEPT - len(kept))]

    build = {
        "nodes": lambda d: {"nodes": [pick(n, NODE_FIELDS) for n in d["nodes"]]},
        "partitions": lambda d: {
            "partitions": [
                {
                    "name": p["name"],
                    "partition": {"state": p["partition"]["state"]},
                    "nodes": {"configured": p["nodes"]["configured"]},
                }
                for p in d["partitions"]
            ]
        },
        "jobs": lambda d: {
            "jobs": [dict(pick(j, JOB_FIELDS), name=f"job{i + 1}") for i, j in enumerate(kept)]
        },
        "qos": lambda d: {"qos": [pick(q, QOS_FIELDS) for q in d["qos"]]},
        "assoc": lambda d: {"associations": [pick(a, ASSOC_FIELDS) for a in d["associations"]]},
        "accounting": lambda d: {"jobs": accounting_jobs(accounting)},
    }
    out = {f: {"meta": d["meta"], **build[f](d)} for f, d in raw.items()}
    for n in out.get("nodes", {"nodes": []})["nodes"]:
        if n.get("reason") not in KEEP_WORDS:
            n["reason"] = "admin note"
    for d in out.values():
        d["meta"] = {
            "slurm": {
                "release": d["meta"]["slurm"]["release"],
                "version": d["meta"]["slurm"]["version"],
                "cluster": "testcluster",
            }
        }

    # A generic name equal to a real one would read as a leak and could be rewritten
    # again by a later replacement.
    clash = set(names.values()) & set(names)
    if clash:
        sys.exit(f"generic names collide with real ones: {sorted(clash)}")

    texts = {}
    for f, d in out.items():
        text = json.dumps(d, indent=1, sort_keys=True)
        for real in sorted(names, key=len, reverse=True):  # longest first
            # Whole names only: "_" and alphanumerics continue a name, so an account
            # that happens to equal a QoS team suffix never rewrites inside the QoS name.
            text = re.sub(_whole(real), names[real], text)
        texts[f] = text
    left = sorted(r for r in names if any(re.search(_whole(r), t) for t in texts.values()))
    if left:
        sys.exit(f"real names survived the rewrite, nothing written: {left}")
    dst.mkdir(parents=True, exist_ok=True)
    for f, text in texts.items():
        (dst / f"{f}.json").write_text(text + "\n")
    counts = ", ".join(
        f"{f} {len(next(v for v in d.values() if isinstance(v, list)))}" for f, d in out.items()
    )
    print(f"wrote {dst}: {counts}; {len(names)} names replaced")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
