#!/usr/bin/env python3
"""Validate a DeepOps Slurm deployment and report a machine-readable verdict.

Run this on a Slurm controller, login, or compute node after deploying
``playbooks/slurm-cluster.yml``. It checks controller reachability, node
health, GPU (GRES) configuration, and optionally runs a single-GPU job.

The default output is one human-readable line per check. With ``--json`` the
script prints a JSON object with stable field names so automation and AI
agents can consume the result directly.

Exit codes: 0 = all checks passed, 1 = one or more checks failed,
2 = usage or environment error (Slurm commands not found).

Note: on DeepOps Slurm clusters, ``nvidia-smi`` in an ordinary SSH session on
a login/compute node may report "No devices were found" because GPUs are
hidden outside Slurm-managed jobs. That is expected; the authoritative GPU
check is the ``srun`` job this script runs.
"""

import argparse
import functools
import getpass
import json
import os
import pwd
import re
import shutil
import sys
from pathlib import Path

# Slurm is read through the cluster tools' one Slurm module (its JSON, its timeouts,
# its node vocabulary), from this checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from cluster_tools import proc, slurm, vocab  # noqa: E402


# (rc, stdout, stderr), never raising; bounded (cluster_tools.proc).
run = functools.partial(proc.outcome, timeout=60)


def summarize_nodes(nodes):
    """(details, states, unavailable) from Slurm's node records (scontrol --json),
    classified the way every cluster tool classifies them (cluster_tools.vocab):
    details is [{name, state, gpus_configured}] by name, states {full state: count},
    unavailable the names of nodes no new job can start on."""
    nodes = sorted(nodes, key=lambda n: n["name"])
    details = [
        {
            "name": n["name"],
            "state": vocab.node_state(n["state"]),
            "gpus_configured": sum(slurm.node_gpus(n["gres"]).values()),
        }
        for n in nodes
    ]
    states = {}
    for d in details:
        states[d["state"]] = states.get(d["state"], 0) + 1
    unavailable = [n["name"] for n in nodes if not vocab.node_schedulable(n["state"])]
    return details, states, unavailable


def gpu_types(nodes):
    """{gpu type: configured count} over the nodes; "" for untyped GPUs."""
    types = {}
    for n in nodes:
        for gtype, count in slurm.node_gpus(n["gres"]).items():
            types[gtype] = types.get(gtype, 0) + count
    return types


def pick_gpu_type(nodes):
    """The GPU type with the most configured GPUs, "" when no GRES names a type."""
    types = {t: c for t, c in gpu_types(nodes).items() if t}
    return min(types, key=lambda t: (-types[t], t)) if types else ""


# What the test job reports after its GPUs, one KEY=VALUE line each (job_output):
#   private_tmp  1 when /tmp is the job's own: Slurm's namespace plugin
#                (slurm_job_private_dirs) mounts it from <base>/<job id>/, so the
#                mount's root names the job
#   vulkan, egl  1 when the job can render on its GPU: NVIDIA's Vulkan driver and EGL
#                vendor (roles/nvidia_driver's full driver; the headless set has
#                neither, and a job then renders on the CPU without an error) and the
#                dispatchers they plug into (the Vulkan loader, glvnd's libEGL), which a
#                package removal can take while NVIDIA's files stay
PROBES = {
    "private_tmp": 'findmnt -n -o FSROOT --mountpoint /tmp | grep -c "/$SLURM_JOB_ID/"',
    "vulkan": "{ ls /usr/share/vulkan/icd.d/nvidia_icd.json /etc/vulkan/icd.d/nvidia_icd.json 2>/dev/null"
    " | grep -q . && ldconfig -p | grep -q 'libvulkan[.]so[.]1 '; } && echo 1 || echo 0",
    "egl": "{ ls /usr/share/glvnd/egl_vendor.d/10_nvidia.json 2>/dev/null"
    " | grep -q . && ldconfig -p | grep -q 'libEGL[.]so[.]1 '; } && echo 1 || echo 0",
}


def gpu_job_command(job_name, gpu_type, partition):
    """The single-GPU test job: named, so a timed-out one can be found and cancelled."""
    cmd = ["srun", "--job-name=" + job_name, "--gpus=%s1" % (gpu_type + ":" if gpu_type else ""), "--time=5"]
    if partition:
        cmd += ["--partition", partition]
    reports = "; ".join('printf "%s=%%s\\n" "$(%s)"' % (key, probe) for key, probe in PROBES.items())
    return cmd + [
        "bash",
        "-c",
        "nvidia-smi --query-gpu=name,driver_version --format=csv,noheader && " + reports,
    ]


def job_output(out):
    """(the GPU lines, {probe: True, False, or None when unreported})."""
    lines = out.splitlines()
    said = dict(line.split("=", 1) for line in lines if line.split("=", 1)[0] in PROBES and "=" in line)
    gpus = [line for line in lines if line.strip() and line.split("=", 1)[0] not in PROBES]
    return gpus, {key: (said[key].strip() not in ("", "0")) if key in said else None for key in PROBES}


def namespace_type():
    """The cluster's NamespaceType ("" for none, or when it cannot be read)."""
    rc, out, _ = run(["scontrol", "show", "config"])
    found = re.findall(r"(?m)^NamespaceType\s*=\s*(\S+)", out) if rc == 0 else []
    return "" if not found or found[-1] in ("(null)", "none") else found[-1]


def login_private_tmp(user, settings=Path("/etc/security"), mountinfo=Path("/proc/self/mountinfo")):
    """(expected, own) for this login's /tmp. Expected when pam_namespace's settings
    (namespace.conf(5) and namespace.d, the first /tmp line) give USER an instance of
    their own by user name, as roles/login_private_dirs does on the login nodes; own
    when this session's /tmp is mounted from it (<prefix><user>). (False, None) when no
    setting asks for one."""
    rule = None
    for path in [settings / "namespace.conf", *sorted((settings / "namespace.d").glob("*.conf"))]:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        rule = rule or next(
            (f for f in (ln.split("#", 1)[0].split() for ln in lines) if f[:1] == ["/tmp"]), None
        )
    if not rule or len(rule) < 3 or rule[2] != "user":
        return False, None
    listed = rule[3].split(",") if len(rule) > 3 else []
    only = bool(listed) and listed[0].startswith("~")  # ~: only the accounts listed get one
    if (user in [listed[0].lstrip("~"), *listed[1:]] if listed else False) != only:
        return False, None
    mounts = [f for f in (ln.split() for ln in mountinfo.read_text().splitlines()) if len(f) > 4]
    tmp = [f for f in mounts if f[4] == "/tmp"]
    if not tmp:
        return True, False
    # The mount's root is relative to its filesystem: where that filesystem is mounted
    # whole (root /) gives the absolute path.
    device, root = tmp[-1][2], tmp[-1][3]
    whole = next((f[4] for f in mounts if f[2] == device and f[3] == "/"), None)
    return True, whole is not None and whole.rstrip("/") + root == rule[1] + user


def main():
    parser = argparse.ArgumentParser(description="Validate a DeepOps Slurm deployment.")
    parser.add_argument("--json", action="store_true", help="emit one JSON object")
    parser.add_argument(
        "--skip-gpu-job",
        action="store_true",
        help="skip the srun single-GPU test job",
    )
    parser.add_argument(
        "--allow-unavailable-nodes",
        action="store_true",
        help="do not fail when some nodes are down, drained, or unknown",
    )
    parser.add_argument(
        "--partition",
        default="",
        help="partition for the GPU test job (default: cluster default)",
    )
    parser.add_argument(
        "--gpu-type",
        default="",
        help="GPU type for the GPU test job (default: the type with the most configured GPUs)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="the driver is the compute-only set (nvidia_driver_headless): do not require GPU graphics",
    )
    parser.add_argument(
        "--gpu-job-timeout",
        type=int,
        default=300,
        help="seconds to wait for the GPU test job (default: 300)",
    )
    args = parser.parse_args()

    if not shutil.which("scontrol"):
        print("error: scontrol not found; run this on a Slurm node", file=sys.stderr)
        return 2

    result = {
        "ok": False,
        "slurm_version": "",
        "controller_reachable": False,
        "nodes_total": 0,
        "nodes_available": 0,
        "nodes_unavailable": 0,
        "node_states": {},
        "nodes": [],
        "gpus_configured": 0,
        "gpu_job_ran": False,
        "gpu_job_ok": False,
        "gpus_visible_in_job": 0,
        "private_tmp_expected": False,
        "private_tmp_in_job": None,
        "graphics_expected": False,
        "graphics_in_job": None,
        "private_tmp_login_expected": False,
        "private_tmp_in_login": None,
        "failures": [],
    }

    nodes = []
    try:
        data = slurm.load("nodes")
    except slurm.Error as e:
        result["failures"].append("reading the nodes failed: %s" % e)
    else:
        nodes = data["nodes"]
        result["slurm_version"] = ((data.get("meta") or {}).get("slurm") or {}).get("release", "")
        result["controller_reachable"] = True
        details, states, unavailable = summarize_nodes(nodes)
        result["nodes"] = details
        result["node_states"] = states
        result["nodes_total"] = len(nodes)
        result["nodes_unavailable"] = len(unavailable)
        result["nodes_available"] = len(nodes) - len(unavailable)
        result["gpus_configured"] = sum(gpu_types(nodes).values())
        if not nodes:
            result["failures"].append("no nodes are defined in Slurm")
        if unavailable and not args.allow_unavailable_nodes:
            down = ", ".join("%s (%s)" % (d["name"], d["state"]) for d in details if d["name"] in unavailable)
            result["failures"].append("%d node(s) unavailable: %s" % (len(unavailable), down))
        if result["gpus_configured"] == 0:
            result["failures"].append("no GPUs (gres/gpu) configured on any node")

    if not args.skip_gpu_job and result["controller_reachable"] and result["gpus_configured"] > 0:
        result["gpu_job_ran"] = True
        job_name = "deepops-validate-%d" % os.getpid()
        cmd = gpu_job_command(job_name, args.gpu_type or pick_gpu_type(nodes), args.partition)
        rc, out, err = run(cmd, timeout=args.gpu_job_timeout)
        if rc == 124:
            # Killing srun leaves its job queued, to take a GPU later with nobody
            # waiting for it: cancel it by its name.
            run(["scancel", "--name", job_name, "--user", getpass.getuser()])
        gpus, seen = job_output(out)
        if rc == 0 and gpus:
            result["gpu_job_ok"] = True
            result["gpus_visible_in_job"] = len(gpus)
            private = result["private_tmp_in_job"] = seen["private_tmp"]
            graphics = seen["vulkan"] and seen["egl"]
            result["graphics_in_job"] = None if seen["vulkan"] is None else bool(graphics)
            result["graphics_expected"] = not args.headless
            if result["graphics_expected"] and not graphics:
                result["failures"].append(
                    "the test job sees no NVIDIA Vulkan driver or EGL vendor: GPU rendering falls back"
                    " to the CPU (the headless driver; --headless if the site wants it)"
                )
            result["private_tmp_expected"] = bool(namespace_type())
            if result["private_tmp_expected"] and not private:
                result["failures"].append(
                    "the test job's /tmp was not its own, though NamespaceType is set"
                    " (unless slurm_job_private_dirs leaves /tmp out)"
                )
        else:
            result["failures"].append("srun GPU job failed: %s" % (err or out or "rc=%s" % rc))

    login = result["private_tmp_login_expected"], result["private_tmp_in_login"] = login_private_tmp(
        pwd.getpwuid(os.getuid()).pw_name
    )
    if login == (True, False):
        result["failures"].append(
            "this login's /tmp is the shared one, though pam_namespace gives each login its own"
            " (roles/login_private_dirs): a session opened before it was deployed (log in again),"
            " or PAM could not set it up (journalctl -t sshd | grep pam_namespace)"
        )

    result["ok"] = not result["failures"]

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for key in (
            "slurm_version",
            "controller_reachable",
            "nodes_total",
            "nodes_available",
            "nodes_unavailable",
            "gpus_configured",
            "gpu_job_ran",
            "gpu_job_ok",
            "private_tmp_in_job",
            "graphics_in_job",
            "private_tmp_in_login",
        ):
            print("%s=%s" % (key, result[key]))
        for failure in result["failures"]:
            print("FAIL: %s" % failure)
        print("ok=%s" % result["ok"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
