"""job_cgroup: whose a process is, from its /proc/<pid>/cgroup, by the kernel's cgroup
structure, never by a directory's name.

    kind, keys = classify(text)

Slurm puts every process of a job under one job directory: on cgroup v2 a child of
slurmstepd.scope (or <node>_slurmstepd.scope) other than "system", slurmstepd's waiting
area; on cgroup v1 a child of /slurm/uid_<uid> (or /slurm_<node>/...). That directory's
name is the job's cgroup KEY, opaque here: job_<id> up to Slurm 25.11 (and with
CgroupJobIdPaths=yes), the job's SLUID from 26.05 on. Which job id a key is comes from
Slurm (scontrol listpids), never from the name. KIND is job (KEYS: the job directories,
one in practice), slurm (in Slurm's hierarchy, in no job), none (outside it) or bad
(not a cgroup file: fail closed). The one other reader is the epilog's bash
(roles/slurm shared/lib/epilog-job-scope.sh); tests/lib/slurm_cgroups.json is the spec
both are tested on. Python 3.9 or newer, standard library only.
"""

import re

LINE = re.compile(r"^[0-9]+:[^:]*:(/.*)$")
SCOPE = re.compile(r"^(?:.+_)?slurmstepd(?:_.+)?\.scope$")
V1_ROOT = re.compile(r"^slurm(?:_.+)?$")
V1_USER = re.compile(r"^uid_[0-9]+$")


def _path(path):
    """(kind, key) of one hierarchy's PATH."""
    parts = [p for p in path.split("/") if p]
    for i, part in enumerate(parts):
        if SCOPE.match(part):
            below = parts[i + 1] if i + 1 < len(parts) else "system"
            return ("slurm", "") if below == "system" else ("job", below)
    if parts and V1_ROOT.match(parts[0]):
        if len(parts) > 2 and V1_USER.match(parts[1]):
            return "job", parts[2]
        return "slurm", ""
    return "none", ""


def classify(text):
    """(kind, [key]) of the contents of /proc/<pid>/cgroup."""
    lines = [line for line in (text or "").splitlines() if line]
    if not lines:
        return "bad", []
    kinds, keys = set(), []
    for line in lines:
        match = LINE.match(line)
        if not match:
            return "bad", []
        kind, key = _path(match[1])
        kinds.add(kind)
        if key and key not in keys:
            keys.append(key)
    if keys:
        return "job", keys
    return ("slurm" if "slurm" in kinds else "none"), []
