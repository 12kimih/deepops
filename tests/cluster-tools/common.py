"""Shared test setup for the cluster tools: import paths, fixtures and a fake Slurm."""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROLES = HERE.parents[1] / "roles"
sys.path[:0] = [str(ROLES / "cluster_tools_lib/files"), str(ROLES / "cluster_tools/files")]
FIXTURES = HERE / "fixtures/slurm-25.11"


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def running(job_id, qos, tres, user="user1", partition="a10-1", nodes="gpu1"):
    """A running job in the 25.11 shape. The capture has none (the cluster was
    drained), so running jobs are added by hand with the fields Slurm prints."""
    return {"job_id": job_id, "name": f"run{job_id}", "user_name": user, "partition": partition,
            "qos": qos, "job_state": ["RUNNING"], "nodes": nodes, "tres_alloc_str": tres,
            "state_reason": "None", "state_description": ""}


def run_tool(module, *args, env=None, path=True):
    """Run a tool's main() in a fresh interpreter against the fake Slurm."""
    e = {"PATH": f"{HERE / 'bin'}:/usr/bin:/bin" if path else "/usr/bin:/bin",
         "FIXTURES": str(FIXTURES), "LANG": "C.UTF-8", "COLUMNS": "120", "USER": "user1",
         **(env or {})}
    code = (f"import sys; sys.path[:0] = {[str(ROLES / 'cluster_tools_lib/files'), str(ROLES / 'cluster_tools/files')]!r}; "
            f"import {module}; sys.exit({module}.main())")
    return subprocess.run([sys.executable, "-I", "-c", code, *args], env=e, capture_output=True, text=True)
