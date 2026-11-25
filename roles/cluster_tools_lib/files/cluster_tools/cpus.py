"""How many CPUs this process may use, for sizing its parallelism.

nproc, os.cpu_count() and a container's default runtime size see the CPUs a process may
run on, never a CPU quota: on a login node every person's slice has CPUQuota (8 CPUs of
48), and a program starting 48 workers there is throttled with everything else in the
slice, the person's own shell included. usable() is the CPUs this process may run on
(its affinity: a Slurm job's or a container's cpuset), capped by every cgroup v2 CPU
quota (cpu.max) from its own cgroup up to the root, and at least 1. A positive integer
in one of the environment variables named overrides it, so a person's own choice wins.

    usable(environ_names=("CHECK_JOBS",))  -> int
    python3 cpus.py [NAME ...]             prints usable(NAMES)

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


if __name__ == "__main__":
    print(usable(sys.argv[1:]))
