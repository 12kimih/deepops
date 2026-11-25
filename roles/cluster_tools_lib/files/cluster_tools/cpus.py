"""How many CPUs this process may use, for sizing its parallelism.

nproc, os.cpu_count() and a container's default runtime size see the CPUs a process may
run on, never a CPU quota: on a login node every person's slice has CPUQuota (8 CPUs of
48), and a program starting 48 workers there is throttled with everything else in the
slice, the person's own shell included. usable() is the CPUs this process may run on
(its affinity: a Slurm job's or a container's cpuset), capped by every cgroup v2 CPU
quota (cpu.max) from its own cgroup up to the root, and at least 1. A positive integer
in one of the environment variables named overrides it, so a person's own choice wins.

per_worker() is what each worker of a Slurm task may use: --cpus-per-task when given,
else the job's CPUs on the node shared by its workers there, one per task or per GPU
(torchrun starts one per GPU), whichever is more; never more than usable(). per_node()
is what the job may use on this node, for a build's compilers: SLURM_CPUS_ON_NODE,
never more than usable(). A login and a task get these as the knobs that size work by
the machine (user_environment_cpu_knobs: roles/cluster_tools' profile.d, roles/slurm's
task prolog), so a library or build starts no more threads than its share.

    usable(environ_names=("CHECK_JOBS",))  -> int
    per_worker()                           -> int
    per_node()                             -> int
    python3 cpus.py [NAME ...]             prints usable(NAMES)
    python3 cpus.py --job                  prints per_worker() and per_node()

Python 3.9 or newer, standard library only: scripts in the repository run this file
directly, and hosts run it as part of cluster_tools.
"""

import math
import os
import sys
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")


def affinity():
    """The CPUs this process may run on (its cpuset), or the CPU count without one."""
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def own_cgroup(proc_cgroup=Path("/proc/self/cgroup")):
    """This process's cgroup v2 path ("/user.slice/..."), or None on cgroup v1 or no /proc."""
    try:
        lines = proc_cgroup.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        hierarchy, _, path = line.partition("::")
        if hierarchy == "0":
            return path
    return None


def quotas(path, root=CGROUP_ROOT):
    """The CPU quota (CPUs, rounded up) of every cgroup from PATH up to the root that has one."""
    found = []
    here = root / path.strip("/")
    while True:
        try:
            limit, period = (here / "cpu.max").read_text().split()
        except (OSError, ValueError):
            limit = "max"
        if limit != "max":
            found.append(max(1, math.ceil(int(limit) / int(period))))
        if here == root or root not in here.parents:
            return found
        here = here.parent


def usable(environ_names=(), environ=None, proc_cgroup=Path("/proc/self/cgroup"), root=CGROUP_ROOT):
    environ = os.environ if environ is None else environ
    for name in environ_names:
        value = environ.get(name, "")
        if value.isdigit() and int(value) > 0:
            return int(value)
    count = affinity()
    path = own_cgroup(proc_cgroup)
    if path is not None:
        count = min([count, *quotas(path, root)])
    return max(1, count)


def _count(value):
    return int(value) if str(value).isdigit() and int(value) > 0 else 0


def tasks_on_node(environ):
    """The tasks of this step on this node: SLURM_STEP_TASKS_PER_NODE (the batch script's
    SLURM_TASKS_PER_NODE) at this node's index, SLURM_NODEID; "2(x3),1" is 2, 2, 2, 1."""
    spec = environ.get("SLURM_STEP_TASKS_PER_NODE") or environ.get("SLURM_TASKS_PER_NODE") or ""
    counts = []
    for part in spec.split(","):
        count, _, repeat = part.partition("(x")
        counts += [_count(count)] * (_count(repeat.rstrip(")")) or 1)
    node = _count(environ.get("SLURM_NODEID", "0"))
    return counts[node] if node < len(counts) else 0


def per_worker(environ=None, proc_cgroup=Path("/proc/self/cgroup"), root=CGROUP_ROOT):
    environ = os.environ if environ is None else environ
    most = usable((), environ, proc_cgroup, root)
    per_task = _count(environ.get("SLURM_CPUS_PER_TASK", ""))
    if per_task:
        return min(per_task, most)
    on_node = _count(environ.get("SLURM_CPUS_ON_NODE", "")) or most
    workers = max(1, tasks_on_node(environ), _count(environ.get("SLURM_GPUS_ON_NODE", "")))
    return max(1, min(on_node // workers, most))


def per_node(environ=None, proc_cgroup=Path("/proc/self/cgroup"), root=CGROUP_ROOT):
    environ = os.environ if environ is None else environ
    most = usable((), environ, proc_cgroup, root)
    return max(1, min(_count(environ.get("SLURM_CPUS_ON_NODE", "")) or most, most))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    print(f"{per_worker()} {per_node()}" if argv == ["--job"] else usable(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
