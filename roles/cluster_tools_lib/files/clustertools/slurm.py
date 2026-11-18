"""Reading Slurm: the one place the cluster's tools talk to it.

Slurm is read through its JSON output only. Free text such as a job name is a plain
value there, so it can never pose as another field, which every text parser of
`scontrol -o` has had to defend against. The JSON is read with plain `--json`, the
newest parser of the installed Slurm: a pinned data_parser version is dropped a few
releases later. So fields are read defensively, and the tests keep a fixture per
Slurm release. Binaries come from PATH, never a fixed prefix.

Python 3.9 or newer, standard library only.
"""

import json
import os
import re
import subprocess
import sys


# A throttled RPC does not fail: the client library sleeps and retries it for ever,
# printing nothing below -v. Every call is therefore bounded, and running out of time
# is reported as what it most likely is. $CLUSTER_TOOLS_TIMEOUT, shared with the shell
# library, overrides the bound.
TIMEOUT = int(os.environ.get("CLUSTER_TOOLS_TIMEOUT") or 20)


class Error(Exception):
    """A Slurm read that did not give an answer. str() is one line on why, `status` the
    exit status a command reports it with. Everything this module raises about Slurm is
    one of these, so a caller catches Slurm's failures and nothing else: a file error
    or a bug of its own is never reported as Slurm's."""

    status = 1


class Missing(Error):
    """The Slurm client is not installed here."""

    status = 127


class Failed(Error):
    """The client ran and failed, or did not answer in time."""


class SchemaError(Error):
    """A reply without what the tools rely on: an error inside it, unreadable JSON, or a
    release that renamed something."""


def run(*cmd):
    """stdout of a Slurm client command; Missing, or Failed when it fails or has not
    answered within TIMEOUT seconds."""
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=TIMEOUT).stdout
    except FileNotFoundError:
        raise Missing(f"{cmd[0]} not found; run this on a Slurm client.") from None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise Failed(f"{cmd[0]}: {failure(e)}") from None


def failure(error):
    """One line on why a Slurm call failed: Slurm's own first line, or the timeout."""
    if isinstance(error, subprocess.TimeoutExpired):
        return f"no answer in {TIMEOUT} s (slurmctld slow, or rate limiting your requests)"
    text = (error.stderr or "").strip()
    return text.splitlines()[0] if text else f"{error.cmd[0]} exited with status {error.returncode}"


# --- What the tools read --------------------------------------------------------
# Every Slurm read the tools make, and the fields they rely on in it. One list, so a
# Slurm upgrade is checked in one place: a release that renames a field fails load() with
# that field's name, never with figures quietly read as zero, and tests check every
# fixture (one per Slurm release) against the same list.

QUERIES = {
    "nodes": (("scontrol", "--json", "show", "nodes"), "nodes"),
    "partitions": (("scontrol", "--json", "show", "partitions"), "partitions"),
    "jobs": (("scontrol", "--json", "show", "jobs"), "jobs"),
    "jobs_detail": (("scontrol", "-d", "--json", "show", "jobs"), "jobs"),
    "qos": (("sacctmgr", "--json", "show", "qos"), "qos"),
    "associations": (("sacctmgr", "--json", "show", "assoc"), "associations"),
}
_JOB_FIELDS = (
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
    "time_limit",
    "start_time",
    "submit_time",
    "priority",
)
FIELDS = {
    "nodes": (
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
        "tres",
        "tres_used",
        "reason",
        "reason_set_by_user",
        "reason_changed_at",
    ),
    "partitions": ("name", "partition", "nodes"),
    "jobs": _JOB_FIELDS,
    "jobs_detail": (*_JOB_FIELDS, "gres_detail"),
    "qos": ("name", "priority", "preempt", "flags", "limits"),
    "associations": ("account", "user", "qos", "default", "is_default"),
}


def check(kind, data):
    """DATA, a parsed reply to QUERIES[KIND], or SchemaError naming what is missing. An
    error Slurm reported inside the reply is raised too: it would otherwise leave an
    empty list that reads as an idle cluster."""
    key = QUERIES[kind][1]
    release = ((data.get("meta") or {}).get("slurm") or {}).get("release", "?")
    errors = [e.get("description") or e.get("error") for e in data.get("errors") or []]
    if errors:
        raise SchemaError(f"Slurm {release} reported an error reading {kind}: {errors[0]}")
    if not isinstance(data.get(key), list):
        raise SchemaError(
            f"Slurm {release} gave no '{key}' list for {kind}; "
            "clustertools.slurm.QUERIES needs updating for this release"
        )
    for record in data[key]:
        missing = [f for f in FIELDS[kind] if f not in record]
        if missing:
            raise SchemaError(
                f"Slurm {release} {kind} lack {', '.join(missing)}; "
                "clustertools.slurm.FIELDS needs updating for this release"
            )
    return data


def load(kind):
    """Read QUERIES[KIND] from Slurm, checked against FIELDS[KIND]."""
    cmd = QUERIES[kind][0]
    try:
        data = json.loads(run(*cmd))
    except ValueError as e:
        raise SchemaError(f"{cmd[0]}: unreadable JSON for {kind}: {e}") from None
    if not isinstance(data, dict):
        raise SchemaError(f"{cmd[0]}: the reply for {kind} is not a JSON object")
    return check(kind, data)


def cluster_name(data):
    """The cluster name any Slurm JSON reply carries in its meta block, or ""."""
    return ((data.get("meta") or {}).get("slurm") or {}).get("cluster") or ""


def num(value):
    """A Slurm JSON number: a {"set", "infinite", "number"} object or a plain int.
    Unset and infinite are None."""
    if isinstance(value, dict):
        return value.get("number") if value.get("set") and not value.get("infinite") else None
    return value


# --- Host lists -----------------------------------------------------------------


def expand_ranges(spec):
    """'0-2,5' -> [0, 1, 2, 5]; ValueError for anything else."""
    out = []
    for part in spec.split(","):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ValueError(f"not an index range: {spec!r}")
        out.extend(range(int(m.group(1)), int(m.group(2) or m.group(1)) + 1))
    return out


_BRACKET = re.compile(r"([^\[]*)\[([^\]]*)\](.*)")
_RANGE = re.compile(r"(\d+)-(\d+)")


def _expand(token):
    m = _BRACKET.fullmatch(token)
    if not m:
        return [token] if token else []
    prefix, spec, suffix = m.groups()
    names = []
    for part in spec.split(","):
        r = _RANGE.fullmatch(part)
        if r:
            lo, hi = r.groups()
            for i in range(int(lo), int(hi) + 1):
                names.extend(_expand(f"{prefix}{i:0{len(lo)}d}{suffix}"))
        elif part:
            names.extend(_expand(prefix + part + suffix))
    return names


def hostnames(nodelist):
    """Expand a Slurm host list ('gpu[1,3-5],rack[1-2]-n[01-02]') into names, keeping
    zero padding. In process: a call to `scontrol show hostnames` per job would
    dominate the run time of anything that walks the job list."""
    if not nodelist or nodelist == "(null)" or nodelist.startswith("None"):
        return []
    names, token, depth = [], "", 0
    for c in nodelist:
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
        elif c == "," and depth == 0:
            names.extend(_expand(token))
            token = ""
            continue
        token += c
    names.extend(_expand(token))
    return names


# --- Jobs ---------------------------------------------------------------------------


def _array_ranges(spec):
    """(first, last, step) for each part of an ArrayTaskId spec (1-100:2,200%10). The
    %N throttle limits how many run at once, not which tasks exist."""
    for part in (spec or "").split("%")[0].split(","):
        m = re.fullmatch(r"(\d+)(?:-(\d+)(?::(\d+))?)?", part)
        if m:
            first = int(m.group(1))
            yield first, int(m.group(2) or first), int(m.group(3) or 1) or 1


def array_tasks(spec):
    """How many tasks a pending array record stands for. Slurm keeps an array's
    unstarted tasks as one record; each is a job to the scheduler and to every limit."""
    return max(sum((last - first) // step + 1 for first, last, step in _array_ranges(spec)), 1)


def array_has(spec, task):
    """Is TASK (an int) among the tasks of the array record with SPEC?"""
    return any(
        first <= task <= last and (task - first) % step == 0 for first, last, step in _array_ranges(spec)
    )


def job_tasks(job):
    """How many jobs a record of `show jobs` stands for: an array's pending tasks, else 1."""
    return array_tasks(job["array_task_string"]) if job.get("array_task_string") else 1


def job_label(job):
    """The job as squeue names it: ARRAY_TASK for an array task (task 0 included), and
    ARRAY_[TASKS] for the record holding an array's tasks that have not started."""
    array, task = num(job.get("array_job_id")), num(job.get("array_task_id"))
    if array and task is not None:
        return f"{array}_{task}"
    if array and job.get("array_task_string"):
        return f"{array}_[{job['array_task_string']}]"
    return str(job["job_id"])


# --- Generic resources (GRES) -------------------------------------------------------


def gres_items(text):
    """(name, type, count, extra) for each item of a GRES string: a node's gres
    ("gpu:a10:4(S:0-1),gpu:2") or a job's gres_detail entry ("gpu:a10:2(IDX:0,2)").
    Commas inside the parentheses belong to the item. type is "" for an untyped item,
    count None when the item gives none."""
    items, token, depth = [], "", 0
    for c in (text or "") + ",":
        depth += (c == "(") - (c == ")")
        if c == "," and depth == 0:
            if token:
                items.append(token)
            token = ""
        else:
            token += c
    out = []
    for item in items:
        head, _, extra = item.partition("(")
        name, *rest = head.split(":")
        count = int(rest.pop()) if rest and rest[-1].isdigit() else None
        out.append((name, ":".join(rest), count, extra.rstrip(")")))
    return out


def node_gpus(gres):
    """{type: count} configured on a node, from its gres string; "" for untyped GPUs."""
    counts = {}
    for name, gtype, count, _ in gres_items(gres):
        if name == "gpu" and count:
            counts[gtype] = counts.get(gtype, 0) + count
    return counts


def job_gpus(job):
    """([(host, index)], {(host, index): gpu type}) from gres_detail, which has one entry
    per allocated node; IDX:N/A is a node where the job holds none. A detail list that
    does not match the node list attributes nothing rather than guessing which node
    holds what."""
    hosts, details = hostnames(job.get("nodes") or ""), job.get("gres_detail") or []
    if details and len(details) != len(hosts):
        print(
            f"job {job['job_id']}: {len(details)} GRES entries for {len(hosts)} nodes; GPUs not attributed",
            file=sys.stderr,
        )
        return [], {}
    gpus, types = [], {}
    for host, detail in zip(hosts, details):
        for name, gtype, _, extra in gres_items(detail):
            key, _, spec = extra.partition(":")
            if name != "gpu" or key != "IDX" or spec == "N/A":
                continue
            for i in expand_ranges(spec):
                gpus.append((host, i))
                types[(host, i)] = gtype
    return gpus, types


# --- Trackable resources (TRES) -----------------------------------------------------


def tres_list(items):
    """{"gres/gpu:a10": 6, "cpu": 8} from a JSON TRES list ([{"type", "name", "count"}])."""
    out = {}
    for t in items or []:
        key = f"{t.get('type')}/{t['name']}" if t.get("name") else t.get("type")
        if key and t.get("count") is not None:
            out[key] = t["count"]
    return out


def tres_str(text):
    """{"cpu": "8", "mem": "64G", "gres/gpu": "2"} from a TRES string. Keys are whole,
    so gres/gpu never answers for gres/gpu:a10."""
    out = {}
    for part in (text or "").split(","):
        key, sep, value = part.partition("=")
        if sep:
            out[key] = value
    return out


_MEM_UNIT = {"K": 1 / 1024, "M": 1, "G": 1024, "T": 1024**2, "P": 1024**3}


def mem_mib(value):
    """MiB from a TRES memory value: plain MiB, or a number with a unit and possibly a
    fraction (1.50T). Anything unreadable is 0."""
    value = str(value or "")
    mult = _MEM_UNIT.get(value[-1:], None)
    number = value[:-1] if mult is not None else value
    try:
        return int(float(number) * (mult if mult is not None else 1))
    except ValueError:
        return 0
