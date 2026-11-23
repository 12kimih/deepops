"""io-usage: who does I/O on this node, by Slurm job and by login, every minute.

    io-usage CONFIG.json

Three sets of node_exporter textfile counters:

  io_usage_job_read_bytes_total, io_usage_job_written_bytes_total {job_id, user}
      Each running job's syscall I/O: rchar and wchar of /proc/<pid>/io summed over
      the processes whose cgroup path names job_<id> (Slurm puts every process of a
      job there, whatever the cgroup layout). A reaped child's counts move into its
      parent's (the kernel adds them at wait()), so tasks that already exited still
      count; a process that leaves taking its counts with it (its parent is not in
      the job: a step ending) is kept as counted, so the counter never goes back. rchar and wchar
      count every read and write call, page cache, pipes and sockets included: they
      rank jobs by how much they read and write, they are not bytes on a disk or on
      the NFS wire (node_exporter's mountstats counts those per mount).
  io_usage_login_read_bytes_total, io_usage_login_written_bytes_total {user, device}
      Each login's block I/O per disk (rbytes and wbytes of io.stat in the user's
      user-<uid>.slice): what people do on the node itself, outside jobs.
  io_usage_slice_pressure_seconds_total {slice, kind}
      How long tasks of each slice waited for I/O (io.pressure: some, full).

and io_usage_last_success_timestamp_seconds. The state file keeps each job's counter
across runs. CONFIG: proc_root, cgroup_root, sys_root, slices (cgroups, relative to
cgroup_root, whose pressure is reported), textfile, state_file. Python 3.9 or newer,
standard library only.
"""

import json
import os
import pwd
import re
import sys
import time
from collections import Counter

from . import config
from .files import write_atomic, write_textfile

CONFIG_KEYS = {"proc_root": str, "cgroup_root": str, "sys_root": str, "slices": list, "textfile": str,
               "state_file": str}  # fmt: skip
JOB = re.compile(r"/job_(\d+)(?:/|$)")
USER_SLICE = re.compile(r"^user-(\d+)\.slice$")


def load_config(path):
    cfg = config.require(path, CONFIG_KEYS)
    if not all(isinstance(s, str) and s for s in cfg["slices"]):
        raise config.ConfigError(f"{path}: slices must be cgroup paths")
    return cfg


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:  # a process or cgroup gone meanwhile
        return None


def name_of(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def process(proc_root, pid):
    """(job id, key, ppid key, uid, rchar, wchar) of PID, or None when it is in no job or
    gone. A key is "pid:start time", so a reused pid is another process."""
    cgroup = read(os.path.join(proc_root, pid, "cgroup"))
    match = JOB.search(cgroup or "")
    if not match:
        return None
    io, status = read(os.path.join(proc_root, pid, "io")), read(os.path.join(proc_root, pid, "status"))
    stat = read(os.path.join(proc_root, pid, "stat"))
    if io is None or status is None or stat is None:
        return None
    fields = dict(line.split(":", 1) for line in io.splitlines() if ":" in line)
    status_fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    uid = int(status_fields.get("Uid", "0").split()[0])
    ppid = status_fields.get("PPid", "0").strip()
    start = stat.rsplit(")", 1)[-1].split()[19]  # field 22, after the command's parentheses
    return match[1], f"{pid}:{start}", ppid, uid, int(fields.get("rchar", 0)), int(fields.get("wchar", 0))


def jobs(proc_root):
    """{job id: {"procs": {key: [rchar, wchar, ppid]}, "uid"}}: the job's live processes,
    and its user, the most common uid among them other than root (root's: slurmstepd)."""
    out, uids = {}, {}
    for pid in os.listdir(proc_root):
        if not pid.isdigit():
            continue
        row = process(proc_root, pid)
        if row is None:
            continue
        job, key, ppid, uid, rchar, wchar = row
        out.setdefault(job, {"procs": {}})["procs"][key] = [rchar, wchar, ppid]
        uids.setdefault(job, Counter())[uid] += 1
    for job, counts in uids.items():
        people = [uid for uid, _ in counts.most_common() if uid != 0]
        out[job]["uid"] = people[0] if people else 0
    return out


def carry(now, state):
    """{job: {"read", "written", "uid"}}: counters that never go back. A process gone since
    the last run whose parent is still one of the job's processes was reaped into it (the
    parent's counts now hold its own); one whose parent is not took its counts away, so
    they are kept as lost. STATE ({job: {"procs", "lost"}}) is updated; jobs gone leave it."""
    out = {}
    for job, seen in now.items():
        prev = state.get(job, {"procs": {}, "lost": [0, 0]})
        lost = list(prev.get("lost", [0, 0]))
        live_pids = {key.split(":")[0] for key in seen["procs"]}
        for key, (rchar, wchar, ppid) in prev.get("procs", {}).items():
            if key not in seen["procs"] and ppid not in live_pids:
                lost[0] += rchar
                lost[1] += wchar
        sums = [sum(p[0] for p in seen["procs"].values()), sum(p[1] for p in seen["procs"].values())]
        out[job] = {"read": sums[0] + lost[0], "written": sums[1] + lost[1], "uid": seen["uid"]}
        state[job] = {"procs": seen["procs"], "lost": lost}
    for job in set(state) - set(now):
        del state[job]
    return out


def device_name(sys_root, device):
    uevent = read(os.path.join(sys_root, "dev/block", device, "uevent")) or ""
    return next(
        (line.split("=", 1)[1] for line in uevent.splitlines() if line.startswith("DEVNAME=")), device
    )


def logins(cgroup_root, sys_root):
    """[(user, device name, rbytes, wbytes)] from each user-<uid>.slice's io.stat."""
    rows = []
    base = os.path.join(cgroup_root, "user.slice")
    for entry in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        match = USER_SLICE.match(entry)
        stat = read(os.path.join(base, entry, "io.stat")) if match else None
        for line in (stat or "").splitlines():
            device, *pairs = line.split()
            values = dict(pair.split("=", 1) for pair in pairs if "=" in pair)
            rows.append((name_of(int(match[1])), device_name(sys_root, device), int(values.get("rbytes", 0)),
                         int(values.get("wbytes", 0))))  # fmt: skip
    return rows


def pressure(cgroup_root, slices):
    """[(slice, kind, seconds)] from each slice's io.pressure totals (microseconds)."""
    rows = []
    for name in slices:
        text = read(os.path.join(cgroup_root, name, "io.pressure")) or ""
        for line in text.splitlines():
            kind, *pairs = line.split()
            total = dict(pair.split("=", 1) for pair in pairs if "=" in pair).get("total")
            if total is not None:
                rows.append((name, kind, int(total) / 1e6))
    return rows


def label(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def textfile(job_rows, login_rows, pressure_rows, now):
    out = []

    def family(name, kind, text, rows):
        out.extend([f"# HELP {name} {text}", f"# TYPE {name} {kind}"])
        for labels, value in rows:
            body = ",".join(f'{k}="{label(v)}"' for k, v in labels.items())
            out.append(f"{name}{{{body}}} {value}")

    for side, verb in (("read", "read"), ("written", "written")):
        family(f"io_usage_job_{side}_bytes_total", "counter",
               f"Bytes the job's processes {verb} through system calls (rchar/wchar; page cache and pipes included).",
               [({"job_id": job, "user": name_of(v["uid"])}, v[side]) for job, v in sorted(job_rows.items())])  # fmt: skip
    family("io_usage_login_read_bytes_total", "counter", "Bytes the login's processes read from the disk (io.stat).",
           [({"user": u, "device": d}, r) for u, d, r, _ in login_rows])  # fmt: skip
    family("io_usage_login_written_bytes_total", "counter", "Bytes the login's processes wrote to the disk (io.stat).",
           [({"user": u, "device": d}, w) for u, d, _, w in login_rows])  # fmt: skip
    family("io_usage_slice_pressure_seconds_total", "counter", "Time the slice's tasks waited for I/O (io.pressure).",
           [({"slice": s, "kind": k}, v) for s, k, v in pressure_rows])  # fmt: skip
    family(
        "io_usage_last_success_timestamp_seconds", "gauge", "When io-usage last counted.", [({}, int(now))]
    )
    return "\n".join(out) + "\n"


def run(cfg, now=None):
    now = time.time() if now is None else now
    try:
        with open(cfg["state_file"]) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    counted = carry(jobs(cfg["proc_root"]), state)
    text = textfile(counted, logins(cfg["cgroup_root"], cfg["sys_root"]), pressure(cfg["cgroup_root"], cfg["slices"]),
                    now)  # fmt: skip
    os.makedirs(os.path.dirname(os.path.abspath(cfg["state_file"])), exist_ok=True)
    write_atomic(cfg["state_file"], json.dumps(state), mode=0o600)
    write_textfile(cfg["textfile"], text)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    try:
        cfg = load_config(argv[0])
    except config.ConfigError as e:
        print(f"io-usage: {e}", file=sys.stderr)
        return 2
    return run(cfg)


if __name__ == "__main__":
    sys.exit(main())
