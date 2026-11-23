"""gpu_diag_idle: NVIDIA's DCGM diagnostics on the GPU nodes no job is using, weekly.

    gpu-diag-idle run CONFIG [--dry-run]   one pass over the idle GPU nodes
    gpu-diag-idle publish CONFIG           the metrics from the kept state, as they are

A pass takes the GPU nodes that are exactly IDLE in Slurm and in no reservation, whose
last diagnosis is older than period_days, oldest first, one at a time, until the next
one would not fit in budget_seconds. For each node it:

  1. holds it: a MAINT reservation for root (without IGNORE_JOBS, so Slurm refuses it
     when a job has landed since the look, and the node is skipped: no user job ever
     shares a node with the diagnostics), lasting two runs and a margin, which also
     frees the node should this program die;
  2. runs `dcgmi diag -r LEVEL -j` as a Slurm job inside it, holding every GPU of the
     node, so accounting and sq show them used and the job's cgroup gives the devices;
  3. reads the verdict: a test result of Fail is the GPUs' verdict; anything else that
     is not a clean pass (the host engine unreachable, a timeout, output it cannot read,
     a non-zero exit without a failed test) is the tool's, never the node's;
  4. a failure is run once more in the same reservation: failed twice, the node is
     drained (drain_on_failure) with the reason "gpu-diag: <tests>", which neither NHC
     nor the maintenance resume lifts; failed then passed, it stays in service and is
     reported as flaky;
  5. deletes the reservation.

The result of each node goes to node_exporter textfile metrics (gpu_diag_idle_*), which the
alerts read, and to a state file; the last raw output of each node is kept beside it.
Python 3.9 or newer, standard library only.
"""

import json
import math
import os
import sys
import time

from . import config, slurm, vocab
from .cli import run_main
from .files import write_atomic
from .prom import series

CONFIG_KEYS = {
    "level": int,
    "timeouts": dict,
    "period_days": (int, float),
    "budget_seconds": int,
    "drain_on_failure": bool,
    "reservation_prefix": str,
    "textfile_dir": str,
    "textfile": str,
    "metrics_file": str,
    "state_file": str,
    "raw_dir": str,
}
# What a pass records for a node. pass, fail and flaky are a diagnosis (they count for
# the period); error and skipped are not.
RESULTS = ("pass", "fail", "flaky", "error", "skipped")
DIAGNOSED = ("pass", "fail", "flaky")
DAY = 86400


def check_config(cfg):
    """CFG, refused (config.ConfigError) where a pass could not run as configured."""
    if str(cfg["level"]) not in {str(k) for k in cfg["timeouts"]}:
        raise config.ConfigError(f"level {cfg['level']} has no timeout in timeouts")
    if cfg["level"] < 1:
        raise config.ConfigError("level must be 1 or more (dcgmi diag -r)")
    return cfg


def load_config(path):
    return check_config(config.require(path, CONFIG_KEYS))


def timeout_of(cfg):
    return int({str(k): v for k, v in cfg["timeouts"].items()}[str(cfg["level"])])


# --- Choosing nodes ----------------------------------------------------------------


def node_gres(node):
    """The --gres request for every GPU of NODE ("gpu:<type>:8", types joined by commas),
    or "" for a node without GPUs."""
    gpus = slurm.node_gpus(node.get("gres"))
    return ",".join(f"gpu:{t}:{n}" if t else f"gpu:{n}" for t, n in sorted(gpus.items()))


def idle(node):
    """Exactly IDLE (no job, no drain, maintenance, reservation or power flag) and in no
    reservation."""
    return vocab.node_state(node["state"]) == "idle" and not node.get("reservation")


def candidates(nodes, state, cfg, now):
    """The GPU nodes a pass diagnoses, oldest diagnosis first: idle, and not diagnosed
    within period_days."""
    due = now - cfg["period_days"] * DAY
    found = [
        n for n in nodes if node_gres(n) and idle(n) and state.get(n["name"], {}).get("diagnosed", 0) <= due
    ]
    return sorted(found, key=lambda n: (state.get(n["name"], {}).get("diagnosed", 0), n["name"]))


# --- One diagnostic run --------------------------------------------------------------


def tests_in(data):
    """[(test name, [result statuses])] found anywhere in DATA, a parsed `dcgmi diag -j`.
    DCGM's documentation shows {"category", "tests": [...]}; its source wraps the
    categories in {"DCGM Diagnostic": {"test_categories": [...]}}: both are read."""
    found = []
    if isinstance(data, dict):
        tests = data.get("tests")
        if isinstance(tests, list):
            for test in tests:
                if isinstance(test, dict) and "name" in test:
                    statuses = [r.get("status", "") for r in test.get("results") or [] if isinstance(r, dict)]
                    summary = (test.get("test_summary") or {}).get("status")
                    found.append((str(test["name"]), statuses + ([summary] if summary else [])))
        for key, value in data.items():
            if key != "tests":
                found += tests_in(value)
    elif isinstance(data, list):
        for item in data:
            found += tests_in(item)
    return found


def runtime_error(data):
    """The runtime_error DCGM reports instead of results, or None."""
    if isinstance(data, dict):
        if data.get("runtime_error"):
            return str(data["runtime_error"])
        for value in data.values():
            found = runtime_error(value)
            if found:
                return found
    return None


def verdict(returncode, output):
    """(result, failed tests, why) of one run: ("pass", [], ""), ("fail", [tests], "")
    when any result says Fail, or ("error", [], why) for everything else: no JSON, a
    runtime error, no test results, or a non-zero exit without a failed test."""
    start = output.find("{")
    try:
        data = json.loads(output[start:]) if start >= 0 else None
    except ValueError:
        data = None
    if data is None:
        return "error", [], f"unreadable output (exit {returncode})"
    error = runtime_error(data)
    if error:
        return "error", [], error
    tests = tests_in(data)
    failed = sorted({name for name, statuses in tests if "Fail" in statuses})
    if failed:
        return "fail", failed, ""
    if not tests:
        return "error", [], f"no test results (exit {returncode})"
    if returncode != 0:
        return "error", [], f"exit {returncode} without a failed test"
    return "pass", [], ""


def diag_args(node, reservation, gres, level, timeout):
    """srun's arguments for one run on NODE inside RESERVATION, holding GRES."""
    minutes = math.ceil(timeout / 60)
    return [
        f"--reservation={reservation}", f"--nodelist={node}", "--nodes=1", "--ntasks=1",
        "--exclusive", f"--gres={gres}", f"--time={minutes}", "--job-name=gpu-diag", "--quiet",
        "dcgmi", "diag", "-r", str(level), "-j",
    ]  # fmt: skip


def run_diag(node, reservation, gres, cfg):
    """(result, failed, why, raw output) of one run on NODE inside RESERVATION."""
    timeout = timeout_of(cfg)
    rc, out, err = slurm.step(
        "srun", *diag_args(node, reservation, gres, cfg["level"], timeout), timeout=timeout + 120
    )
    result, failed, why = verdict(rc, out)
    if result == "error" and err:
        why = f"{why}: {err.splitlines()[-1]}"
    return result, failed, why, out


# --- One node ------------------------------------------------------------------------


def reservation_args(name, node, minutes):
    """scontrol's arguments for the reservation that holds NODE."""
    return [
        "create", "reservation", f"ReservationName={name}", f"Nodes={node}",
        "StartTime=now", f"Duration={minutes}", "Users=root", "Flags=MAINT",
    ]  # fmt: skip


def diagnose(node, cfg, dry_run=False, log=print):
    """The record of one node: hold it, run (and confirm a failure), free it, drain it
    on a confirmed failure. Every Slurm call that fails is the tool's error, never the
    node's."""
    name, gres, timeout = f"{cfg['reservation_prefix']}-{node['name']}", node_gres(node), timeout_of(cfg)
    hold = reservation_args(name, node["name"], math.ceil((2 * timeout + 300) / 60))
    record = {"result": "error", "failed": [], "why": "", "drained": False, "raw": ""}
    if dry_run:
        log("would run: scontrol " + " ".join(hold))
        log("would run: srun " + " ".join(diag_args(node["name"], name, gres, cfg["level"], timeout)))
        return {**record, "result": "skipped", "why": "dry run"}
    try:
        slurm.run("scontrol", *hold)
    except slurm.Error as e:  # a job landed, or an overlapping reservation: not ours to take
        return {**record, "result": "skipped", "why": str(e)}
    try:
        result, failed, why, raw = run_diag(node["name"], name, gres, cfg)
        if result == "fail":
            again, failed_again, why, raw = run_diag(node["name"], name, gres, cfg)
            result = {"fail": "fail", "pass": "flaky"}.get(again, "error")
            failed = failed_again if again == "fail" else failed
            if again == "error":
                why = f"failed ({', '.join(failed)}), then the rerun could not run: {why}"
        record.update(result=result, failed=failed, why=why, raw=raw)
    finally:
        try:
            slurm.run("scontrol", "delete", f"ReservationName={name}")
        except slurm.Error as e:  # it ends by itself at its Duration
            log(f"gpu-diag-idle: {name} stays until it ends: {e}", file=sys.stderr)
    if record["result"] == "fail" and cfg["drain_on_failure"]:
        reason = "gpu-diag: " + ",".join(record["failed"])
        try:
            slurm.run("scontrol", "update", f"nodename={node['name']}", "state=drain", f"reason={reason}")
            record["drained"] = True
        except slurm.Error as e:
            record["why"] = f"not drained: {e}"
    return record


# --- Metrics -------------------------------------------------------------------------


def textfile(state, pass_started, first_pass=0):
    """The metrics for STATE ({node: record}), the time the last pass started and the
    time the first one did (0 before any), from which a node counts as overdue."""
    lines = [
        "# HELP gpu_diag_idle_last_success_timestamp_seconds When the last pass over the idle GPU nodes started.",
        "# TYPE gpu_diag_idle_last_success_timestamp_seconds gauge",
        series("gpu_diag_idle_last_success_timestamp_seconds", {}, int(pass_started)),
        "# HELP gpu_diag_idle_since_seconds When the first pass ran (0: none yet).",
        "# TYPE gpu_diag_idle_since_seconds gauge",
        series("gpu_diag_idle_since_seconds", {}, int(first_pass)),
        "# HELP gpu_diag_idle_node_last_success_timestamp_seconds When a node's GPUs were last diagnosed (pass, fail or flaky).",
        "# TYPE gpu_diag_idle_node_last_success_timestamp_seconds gauge",
    ]
    for node, rec in sorted(state.items()):
        if rec.get("diagnosed"):
            lines.append(
                series(
                    "gpu_diag_idle_node_last_success_timestamp_seconds", {"node": node}, int(rec["diagnosed"])
                )
            )
    lines += [
        "# HELP gpu_diag_idle_result 1 for the result of a node's last attempt; tests names the failed ones.",
        "# TYPE gpu_diag_idle_result gauge",
    ]
    for node, rec in sorted(state.items()):
        labels = {"node": node, "result": rec["result"], "tests": ",".join(rec.get("failed") or []),
                  "drained": "yes" if rec.get("drained") else "no"}  # fmt: skip
        lines.append(series("gpu_diag_idle_result", labels, 1))
    return "\n".join(lines) + "\n"


def publish(cfg, kept):
    """Write the metrics of KEPT (read_state) to the kept file and the textfile directory."""
    text = textfile(kept["nodes"], kept["pass_started"], kept["first_pass"])
    for path in (cfg["metrics_file"], os.path.join(cfg["textfile_dir"], cfg["textfile"])):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_atomic(path, text)


def read_state(cfg):
    try:
        with open(cfg["state_file"]) as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"pass_started": 0, "first_pass": 0, "nodes": {}}
    return {
        "pass_started": data.get("pass_started", 0),
        "first_pass": data.get("first_pass", 0),
        "nodes": data.get("nodes", {}),
    }


# --- The pass ------------------------------------------------------------------------


def run_pass(cfg, dry_run=False, now=None, log=print):
    started = time.time() if now is None else now
    kept = read_state(cfg)
    nodes = candidates(slurm.load("nodes")["nodes"], kept["nodes"], cfg, started)
    per_node = 2 * timeout_of(cfg) + 300
    for node in nodes:
        if time.time() - started + per_node > cfg["budget_seconds"] and not dry_run:
            log(f"gpu-diag-idle: budget reached; {node['name']} and later wait for the next pass")
            break
        rec = diagnose(node, cfg, dry_run=dry_run, log=log)
        log(f"gpu-diag-idle: {node['name']}: {rec['result']} {','.join(rec['failed'])} {rec['why']}".rstrip())
        if dry_run:
            continue
        previous = kept["nodes"].get(node["name"], {})
        if rec["raw"]:
            os.makedirs(cfg["raw_dir"], exist_ok=True)
            write_atomic(os.path.join(cfg["raw_dir"], f"{node['name']}.json"), rec["raw"])
        kept["nodes"][node["name"]] = {
            "result": rec["result"], "failed": rec["failed"], "why": rec["why"], "drained": rec["drained"],
            "attempted": int(time.time()),
            "diagnosed": int(time.time()) if rec["result"] in DIAGNOSED else previous.get("diagnosed", 0),
        }  # fmt: skip
    if dry_run:
        return 0
    kept["pass_started"] = int(started)
    kept["first_pass"] = kept["first_pass"] or int(started)
    os.makedirs(os.path.dirname(cfg["state_file"]), exist_ok=True)
    write_atomic(cfg["state_file"], json.dumps(kept, indent=1, sort_keys=True))
    publish(cfg, kept)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = [a for a in argv if a != "--dry-run"]
    if len(args) != 2 or args[0] not in ("run", "publish"):
        print("usage: gpu-diag-idle run|publish CONFIG [--dry-run]", file=sys.stderr)
        return 2

    def body():
        cfg = load_config(args[1])
        if args[0] == "publish":
            publish(cfg, read_state(cfg))
            return 0
        return run_pass(cfg, dry_run="--dry-run" in argv)

    return run_main("gpu-diag-idle", body)


if __name__ == "__main__":
    sys.exit(main())
