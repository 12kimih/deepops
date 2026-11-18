"""A busy cluster for the sq tests, built on the real (drained) Slurm 25.11 fixture.

The capture was taken while every node was drained and no job ran, so the states and
running jobs sq must handle are set here, in the shapes Slurm prints:
gpu1 mixed (user1's job), gpu2 allocated, gpu3 idle, gpu4 mixed+drain, gpu5 powered
down, gpu6 shared by a30-2 and a new debug partition, gpu7 down and not responding,
gpu8 idle. Jobs: running ones on gpu1, gpu2 and gpu6 (one per partition sharing it),
a completed one that still carries an allocation, a pending job over a per-job limit,
and a pending job whose name imitates other fields.
"""

import copy
import json
import time
from pathlib import Path

from common import FIXTURES, load


def value(n):
    return {"set": True, "infinite": False, "number": n}


STATES = {
    "gpu1": ["MIXED"], "gpu2": ["ALLOCATED"], "gpu3": ["IDLE"], "gpu4": ["MIXED", "DRAIN"],
    "gpu5": ["IDLE", "POWERED_DOWN"], "gpu6": ["MIXED"], "gpu7": ["DOWN", "NOT_RESPONDING"], "gpu8": ["IDLE"],
}  # fmt: skip
ALLOC = {"gpu1": (40, 230400, 4), "gpu2": (92, 507424, 8), "gpu4": (20, 102400, 2), "gpu6": (48, 204800, 3)}


def running(job_id, user, partition, nodes, cpu, mem_g, gres, start=None, **extra):
    start = int(time.time()) - 3600 if start is None else start  # an hour in
    gtype = "a10" if partition.startswith("a10") else "a30"
    return {
        "job_id": job_id, "name": f"run{job_id}", "user_name": user, "account": "acct1", "partition": partition,
        "qos": "high_t1", "job_state": ["RUNNING"], "state_reason": "None", "state_description": "",
        "nodes": nodes, "node_count": value(1), "cpus": value(cpu),
        "tres_req_str": f"cpu={cpu},mem={mem_g}G,node=1,gres/gpu={gres}",
        "tres_alloc_str": f"cpu={cpu},mem={mem_g}G,node=1,billing={cpu},gres/gpu={gres},gres/gpu:{gtype}={gres}",
        "tres_per_node": f"gres/gpu:{gtype}:{gres}", "array_job_id": value(0),
        "array_task_id": {"set": False, "infinite": False, "number": 0}, "array_task_string": "",
        "gres_detail": [], "time_limit": value(1440), "start_time": value(start), "submit_time": value(start - 60),
        "priority": value(100), **extra,
    }  # fmt: skip


def write(directory):
    """Write the scenario's nodes, partitions, jobs, qos and assoc JSON into DIRECTORY."""
    nodes, parts, jobs = load("nodes"), load("partitions"), load("jobs")
    for n in nodes["nodes"]:
        n["state"] = STATES[n["name"]]
        cpu, mem, gres = ALLOC.get(n["name"], (0, 0, 0))
        n["alloc_cpus"], n["alloc_memory"] = cpu, mem
        gtype = "a10" if "a10" in n["tres"] else "a30"
        n["tres_used"] = f"cpu={cpu},mem={mem}M,gres/gpu={gres},gres/gpu:{gtype}={gres}" if cpu else ""
        if n["name"] == "gpu6":
            n["partitions"] = ["a30-2", "debug"]
    debug = copy.deepcopy(next(p for p in parts["partitions"] if p["name"] == "a30-2"))
    debug["name"], debug["nodes"]["configured"] = "debug", "gpu6"
    parts["partitions"].append(debug)

    pending = jobs["jobs"]
    over = copy.deepcopy(pending[0])
    over.update(
        job_id=90001,
        name="toobig",
        user_name="user2",
        state_reason="QOSMaxGRESPerJob",
        tres_req_str="cpu=8,node=1,gres/gpu=16",
        array_task_string="",
    )
    hostile = copy.deepcopy(pending[0])
    hostile.update(
        job_id=90002,
        name="x UserId=root(0) JobState=RUNNING Partition=evil",
        user_name="user3",
        array_task_string="",
        state_reason="Priority",
    )
    done = running(90003, "user9", "a10-1", "gpu8", 8, 32, 1)
    done["job_state"] = ["COMPLETED"]
    jobs["jobs"] = [
        running(90010, "user1", "a10-2", "gpu1", 40, 225, 4),
        running(90011, "user2", "a30-1", "gpu2", 92, 495, 8),
        running(90012, "user1", "a30-2", "gpu6", 32, 150, 2),
        running(90013, "user4", "debug", "gpu6", 16, 50, 1),
        running(90014, "user2", "a30-1", "gpu4", 20, 100, 2),
        done,
        over,
        hostile,
        *pending,
    ]
    Path(directory).mkdir(parents=True, exist_ok=True)
    for name, data in (("nodes", nodes), ("partitions", parts), ("jobs", jobs)):
        (Path(directory) / f"{name}.json").write_text(json.dumps(data))
    for name in ("qos", "assoc"):
        (Path(directory) / f"{name}.json").write_text((FIXTURES / f"{name}.json").read_text())
