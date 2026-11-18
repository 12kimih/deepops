#!/usr/bin/env python3
"""Make test fixtures for the cluster tools from a real cluster's Slurm JSON.

Capture on a Slurm client (read-only):
    scontrol --json show nodes > nodes.json;  scontrol --json show partitions > partitions.json
    scontrol -d --json show jobs > jobs.json; sacctmgr --json show qos > qos.json
    sacctmgr --json show assoc > assoc.json
then run:  make_fixtures.py CAPTURE_DIR fixtures/slurm-<release>

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

NODE_FIELDS = ("name", "state", "partitions", "cpus", "alloc_cpus", "effective_cpus",
               "real_memory", "alloc_memory", "specialized_memory", "gres", "gres_used",
               "tres", "tres_used", "reason", "reason_set_by_user", "reason_changed_at")
JOB_FIELDS = ("job_id", "name", "user_name", "account", "partition", "qos", "job_state",
              "state_reason", "state_description", "nodes", "node_count", "cpus",
              "tres_req_str", "tres_alloc_str", "tres_per_node", "array_job_id",
              "array_task_id", "array_task_string", "gres_detail", "time_limit",
              "start_time", "submit_time", "priority")
QOS_FIELDS = ("name", "priority", "preempt", "flags", "limits")
ASSOC_FIELDS = ("account", "user", "qos", "default", "is_default", "partition")
JOBS_KEPT = 40


def _whole(name):
    """A pattern for NAME as a whole name; a "_suffix" only where it ends a name."""
    if name.startswith("_"):
        return re.escape(name) + r"(?![A-Za-z0-9_])"
    return rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"


def pick(d, fields):
    return {k: d[k] for k in fields if k in d}


def main(src, dst):
    src, dst = Path(src), Path(dst)
    raw = {f: json.loads((src / f"{f}.json").read_text())
           for f in ("nodes", "partitions", "jobs", "qos", "assoc")}

    # Names to replace, discovered from the data.
    names = {}
    types = sorted({t for n in raw["nodes"]["nodes"]
                    for t in re.findall(r"gpu:([^:,(]+):", n.get("gres") or "")})
    for real, generic in zip(types, GPU_NAMES):
        names[real] = generic
    users = sorted({j["user_name"] for j in raw["jobs"]["jobs"]}
                   | {a["user"] for a in raw["assoc"]["associations"] if a.get("user")}
                   | {n.get("reason_set_by_user") for n in raw["nodes"]["nodes"]} - {None, ""})
    for i, u in enumerate(u for u in users if u not in KEEP_WORDS):
        names[u] = f"user{i + 1}"
    accounts = sorted({a["account"] for a in raw["assoc"]["associations"]})
    for i, a in enumerate(a for a in accounts if a not in KEEP_WORDS and a not in names):
        names[a] = f"acct{i + 1}"
    suffixes = sorted({q["name"].split("_", 1)[1] for q in raw["qos"]["qos"] if "_" in q["name"]})
    for i, s in enumerate(suffixes):
        names[f"_{s}"] = f"_t{i + 1}"   # numbered: a letter could equal a real suffix
    for q in raw["qos"]["qos"]:
        if "_" not in q["name"] and q["name"] not in KEEP_WORDS:
            names[q["name"]] = f"qos{len(names)}"

    jobs = raw["jobs"]["jobs"]
    seen, kept = set(), []
    for j in jobs:                      # one job per kind first, then fill up
        kind = (tuple(j["job_state"]), j.get("state_reason"), j.get("qos"), bool(j.get("array_task_string")))
        if kind not in seen:
            seen.add(kind)
            kept.append(j)
    kept += [j for j in jobs if j not in kept][:max(0, JOBS_KEPT - len(kept))]

    out = {
        "nodes": {"meta": raw["nodes"]["meta"], "nodes": [pick(n, NODE_FIELDS) for n in raw["nodes"]["nodes"]]},
        "partitions": {"meta": raw["partitions"]["meta"],
                       "partitions": [{"name": p["name"], "partition": {"state": p["partition"]["state"]},
                                       "nodes": {"configured": p["nodes"]["configured"]}}
                                      for p in raw["partitions"]["partitions"]]},
        "jobs": {"meta": raw["jobs"]["meta"],
                 "jobs": [dict(pick(j, JOB_FIELDS), name=f"job{i + 1}") for i, j in enumerate(kept)]},
        "qos": {"meta": raw["qos"]["meta"], "qos": [pick(q, QOS_FIELDS) for q in raw["qos"]["qos"]]},
        "assoc": {"meta": raw["assoc"]["meta"],
                  "associations": [pick(a, ASSOC_FIELDS) for a in raw["assoc"]["associations"]]},
    }
    for n in out["nodes"]["nodes"]:
        if n.get("reason") not in KEEP_WORDS:
            n["reason"] = "admin note"
    for d in out.values():
        d["meta"] = {"slurm": {"release": d["meta"]["slurm"]["release"],
                               "version": d["meta"]["slurm"]["version"], "cluster": "testcluster"}}

    # A generic name equal to a real one would read as a leak and could be rewritten
    # again by a later replacement.
    clash = set(names.values()) & set(names)
    if clash:
        sys.exit(f"generic names collide with real ones: {sorted(clash)}")

    texts = {}
    for f, d in out.items():
        text = json.dumps(d, indent=1, sort_keys=True)
        for real in sorted(names, key=len, reverse=True):     # longest first
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
    print(f"wrote {dst}: {len(out['jobs']['jobs'])} jobs, {len(out['nodes']['nodes'])} nodes, "
          f"{len(names)} names replaced")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
