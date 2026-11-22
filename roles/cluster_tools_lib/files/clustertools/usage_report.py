"""Post a Slurm GPU usage report to Slack and notify owners of idle GPU jobs.

Reads jobs and node state from Slurm's JSON output, per-GPU utilisation from
Prometheus (dcgm-exporter). Posts a summary card with the detail in its thread, and
mentions (or DMs) the owner of each job whose GPUs all stayed idle for the whole
window. Can optionally cancel jobs idle for longer.

With --weekly it posts the week instead: for each person, the GPU-hours their jobs held
and how many of them sat idle, the people who idled most mentioned, from the per-job
GPU usage Prometheus keeps (job_device:dcgm_gpu_util_ratio:max). Seeing it is the
point: no job is touched. Someone who idled heavily (repeat_idle_gpu_hours and
repeat_idle_share) for repeat_weeks weeks in a row is reported to the administrators'
channel with the weeks' numbers, their jobs now and what to do.

With --storage it posts the day's disk usage instead (clustertools.storage_report, from
the disk quota metrics): every counted filesystem, its largest people (config people,
the roster's) and projects, and everyone past a warn level, mentioned or sent a DM
(storage_notify). Standard library only.
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

from . import storage_report
from .files import write_atomic
from .prometheus import Prometheus
from .render import duration
from .site import info as site_info
from .slack import MAX_BLOCKS, Slack, bar, sections, table
from .slack import member as slack_member
from .slurm import (
    Error,
    Failed,
    job_gpus,
    job_label,
    job_tasks,
    load,
    node_gpus,
    num,
    run,
    tres_str,
)
from .vocab import job_holds, node_unavailable

NOTIFY = {"mention", "dm"}
DM_FIELDS = {"job": "1234_5", "gpus": 8, "hours": "2", "runtime": "1:00:00"}
WEEK_DAYS = 7
WEEK_TOP = 5  # the idlest people mentioned in the weekly summary
WEEKS_KEPT = 8  # weeks of each person's numbers the weekly summary remembers


# --- Slurm ----------------------------------------------------------------------
# Reading Slurm is clustertools.slurm's job (the cluster_tools_lib role): one reader for
# every tool. What is left here is what this report makes of it.


def parse_jobs(data, now):
    """Jobs holding an allocation, with the GPUs each holds, and pending jobs per user,
    from `scontrol -d --json show job`. Holding is vocab.job_holds, as sq and the metrics
    count it; only a running job can be idle (a suspended one is not computing by
    design)."""
    running = []
    pending = defaultdict(lambda: {"jobs": 0, "gpus": 0, "reasons": Counter(), "wait": 0})
    for job in data["jobs"]:
        state = job.get("job_state") or []
        if job_holds(state):
            gpus, types = job_gpus(job)
            running.append(
                {
                    "id": str(job["job_id"]),
                    "label": job_label(job),
                    "user": job["user_name"],
                    "running": "RUNNING" in state,
                    "partition": job.get("partition", ""),
                    "qos": job.get("qos", ""),
                    # srun and salloc sessions: someone holds the GPUs at a shell
                    "interactive": job.get("batch_flag") is False,
                    "runtime": max(0, now - (num(job.get("start_time")) or now)),
                    "gpus": gpus,
                    "gpu_type": ",".join(sorted({t for t in types.values() if t})),
                }
            )
        elif "PENDING" in state:
            entry = pending[job["user_name"]]
            # A pending array record is all its unstarted tasks, each asking for the
            # record's request, as sq and the scheduler count them.
            tasks = job_tasks(job)
            entry["jobs"] += tasks
            entry["gpus"] += tasks * int(tres_str(job.get("tres_req_str")).get("gres/gpu") or 0)
            entry["reasons"][job.get("state_reason") or "None"] += tasks
            submit = num(job.get("submit_time"))
            if submit:
                entry["wait"] = max(entry["wait"], now - submit)
    return running, dict(pending)


def parse_nodes(data):
    """({node: gpu count}, [(node, state, since, user, reason)]) from `scontrol --json show nodes`."""
    gpus, unavailable = {}, []
    for node in data["nodes"]:
        gpus[node["name"]] = sum(node_gpus(node.get("gres")).values())
        state = node_unavailable(node.get("state") or [])
        if state:
            changed = num(node.get("reason_changed_at"))
            since = datetime.fromtimestamp(changed).isoformat(timespec="seconds") if changed else ""
            unavailable.append(
                (node["name"], state, since, node.get("reason_set_by_user") or "", node.get("reason") or "")
            )
    return gpus, sorted(unavailable)


def collect_slurm(now):
    jobs, pending = parse_jobs(load("jobs_detail"), now)
    nodes, reasons = parse_nodes(load("nodes"))
    return {"jobs": jobs, "nodes": nodes, "reasons": reasons, "pending": pending}


# --- Prometheus -----------------------------------------------------------------


def per_gpu(prom, expr):
    """{(host, Slurm GPU index): value} for a per-GPU DCGM expression.

    Slurm numbers GPUs by device minor number (/dev/nvidiaN); dcgm-exporter's gpu and
    device labels are both the NVML index, in PCI order, which can differ. Keyed on
    either, a busy job could read another GPU's idleness and be cancelled. So the
    expression is relabelled through host_gpu:dcgm_slurm_device:info (rules/slurm.yml),
    made from the minor number DCGM reports: one mapping for the alerts, the
    dashboard and this report. A GPU the mapping lacks has no data, never idle."""
    expr = f"max by (host, gpu) ({expr}) * on (host, gpu) group_left (device) host_gpu:dcgm_slurm_device:info"
    out = {}
    for r in prom.query(expr):
        m = r["metric"]
        dev = re.fullmatch(r"nvidia(\d+)", m.get("device", ""))
        if not dev:
            continue  # not a GPU series: no index Slurm would know
        host = m.get("host") or m.get("instance", "").rsplit(":", 1)[0]
        out[(host, int(dev.group(1)))] = float(r["value"][1])
    return out


def collect_gpus(prom, window):
    return {
        # Percent, the unit idle_util_threshold is set in; DCGM reports a 0 to 1 ratio.
        "util_max": per_gpu(prom, f"100 * max_over_time(DCGM_FI_DEV_GPU_UTIL_RATIO[{window}s])"),
        "util": per_gpu(prom, "100 * avg_over_time(DCGM_FI_DEV_GPU_UTIL_RATIO[10m])"),
        "mem_gib": per_gpu(prom, "DCGM_FI_DEV_FB_USED_BYTES / 2^30"),
    }


def collect_week(prom, threshold, days=WEEK_DAYS):
    """[{user, held, idle}] in GPU-hours over the last DAYS: each minute a job held a GPU,
    and each such minute its utilisation was at or under THRESHOLD percent."""
    held_expr = f"job_device:dcgm_gpu_util_ratio:max[{days}d:1m]"
    idle_expr = f"(job_device:dcgm_gpu_util_ratio:max <= {threshold / 100:g})[{days}d:1m]"
    rows = defaultdict(lambda: {"held": 0.0, "idle": 0.0})
    for field, expr in (("held", held_expr), ("idle", idle_expr)):
        for r in prom.query(f"sum by (user) (count_over_time({expr})) / 60"):
            rows[r["metric"].get("user", "")][field] = float(r["value"][1])
    return sorted(
        ({"user": u, **v} for u, v in rows.items() if u), key=lambda r: (-r["idle"], -r["held"], r["user"])
    )


def render_week(rows, who, days=WEEK_DAYS, top=WEEK_TOP):
    """(text, blocks): totals, then everyone who held GPUs, idlest first; the TOP idlest
    with idle hours are mentioned."""
    held = sum(r["held"] for r in rows)
    idle = sum(r["idle"] for r in rows)
    share = idle / held if held else 0
    head = f"GPU usage, last {days} days: {held:,.0f} GPU-hours held, {idle:,.0f} idle ({share:.0%})"
    lines = []
    for n, r in enumerate(rows):
        name = who(r["user"], n < top and r["idle"] >= 1)
        part = r["idle"] / r["held"] if r["held"] else 0
        lines.append(f"{name}: {r['idle']:,.0f} of {r['held']:,.0f} GPU-hours idle ({part:.0%})")
    body = "\n".join(lines) or "No GPU jobs ran."
    note = (
        "Idle: a GPU a job held at 0% utilisation for the minute. Free GPUs you are not using with scancel."
    )
    return head, [{"type": "header", "text": {"type": "plain_text", "text": head[:150]}}] + sections(body) + [
        {"type": "context", "elements": [{"type": "mrkdwn", "text": note}]}
    ]


# --- Analysis -------------------------------------------------------------------


def analyze(jobs, gpu, window, threshold):
    """Annotate jobs with utilisation and idleness; return the idle ones.

    A job is idle when it has run for the whole window and every GPU it holds stayed at
    or under the threshold throughout. GPUs without data never count as idle.
    """
    idle = []
    for j in jobs:
        peaks = [gpu["util_max"][g] for g in j["gpus"] if g in gpu["util_max"]]
        recent = [gpu["util"][g] for g in j["gpus"] if g in gpu["util"]]
        j["util"] = sum(recent) / len(recent) if recent else None
        # Unknown is not zero: None when no GPU reports memory, and the count of GPUs that do.
        mem = [gpu["mem_gib"][g] for g in j["gpus"] if g in gpu["mem_gib"]]
        j["mem_gib"] = sum(mem) if mem else None
        j["mem_known"] = len(mem)
        j["idle_gpus"] = sum(1 for v in peaks if v <= threshold)
        j["idle"] = (
            j["running"]
            and bool(j["gpus"])
            and j["runtime"] >= window
            and len(peaks) == len(j["gpus"])
            and j["idle_gpus"] == len(j["gpus"])
        )
        if j["idle"]:
            idle.append(j)
    return idle


def track_idle(idle, state, now, window, repeat, notify):
    """Remember when each job turned idle; mark jobs whose owner is due a notification."""
    ids = {j["id"] for j in idle}
    for jid in [k for k in state if k not in ids]:
        del state[jid]
    for j in idle:
        s = state.setdefault(j["id"], {"since": now - window, "notified": None})
        j["state"] = s
        j["notify"] = bool(notify) and (s["notified"] is None or now - s["notified"] >= repeat)


# --- Rendering ------------------------------------------------------------------


def gib(job):
    """GPU memory in use: 'n/a' without data, a trailing '+' when some GPUs lack it."""
    if job["mem_gib"] is None:
        return "n/a"
    return f"{job['mem_gib']:.0f}G" + ("+" if job["mem_known"] < len(job["gpus"]) else "")


def render(report, cfg, who):
    """Summary (text, blocks) and the thread messages [(text, blocks)]."""
    jobs, idle, total = report["jobs"], report["idle"], max(report["total_gpus"], 1)
    alloc = sum(len(j["gpus"]) for j in jobs)
    utils = [j["util"] for j in jobs if j["util"] is not None for _ in j["gpus"]]
    # Unknown is not zero: with no utilisation data (no jobs, or Prometheus down) say so.
    avg = f"{sum(utils) / len(utils):.0f}%" if utils else "n/a"
    pending = report["pending"]
    pend_jobs = sum(p["jobs"] for p in pending.values())
    pend_gpus = sum(p["gpus"] for p in pending.values())
    stamp = datetime.fromtimestamp(report["now"]).strftime("%Y-%m-%d %H:%M")

    text = (
        f"GPU usage {stamp}: {alloc}/{total} GPUs allocated ({100 * alloc / total:.0f}%), "
        f"avg util {avg}, {len(idle)} idle jobs"
    )
    status = ":large_green_circle:" if not idle and not report["reasons"] else ":large_yellow_circle:"
    fields = [
        f"*Allocated*\n{alloc} / {total} GPU ({100 * alloc / total:.0f}%)",
        f"*Average utilisation*\n{avg}",
        f"*Idle jobs*\n{len(idle)}" + (" :zzz:" if idle else ""),
        f"*Pending*\n{pend_jobs} jobs, {pend_gpus} GPU",
        f"*Drain / down*\n{len(report['reasons'])} nodes" + (" :warning:" if report["reasons"] else ""),
    ]
    footer = "Details in the thread"
    if report.get("dashboard_url"):
        footer += f"  |  <{report['dashboard_url']}|Grafana>"
    summary = [
        {"type": "header", "text": {"type": "plain_text", "text": f"\U0001f4ca GPU usage  {stamp}"}},
        {"type": "section", "fields": [{"type": "mrkdwn", "text": f} for f in fields]},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"{status}  {footer}"}]},
    ]

    blocks = []
    if idle:
        rule = f"every GPU at or under {cfg['idle_util_threshold']:g}% for {cfg['idle_hours']:g}h+"
        lines = [f":zzz: *Idle GPU jobs*  _{rule}_"]
        for j in sorted(idle, key=lambda j: -len(j["gpus"])):
            hosts = ",".join(sorted({h for h, _ in j["gpus"]}))
            lines.append(
                f"• {who(j['user'], 'mention' in cfg['notify'] and j['notify'])}  "
                f"`{j['label']}`  {len(j['gpus'])}x {j['gpu_type'] or 'GPU'} on {hosts}  "
                f"running {duration(j['runtime'])}"
            )
        lines.append("_Cancel with `scancel <job>` if you no longer need it._")
        blocks.extend(sections("\n".join(lines)))
    if report["reasons"]:
        rows = [(n, st, since.replace("T", " ")[:16], u, why) for n, st, since, u, why in report["reasons"]]
        blocks.extend(
            sections(
                ":warning: *Drain / down nodes*\n" + table(("NODE", "STATE", "SINCE", "BY", "REASON"), rows)
            )
        )
    if pending:
        rows = [
            (
                u,
                p["jobs"],
                p["gpus"],
                duration(p["wait"]),
                ", ".join(f"{r.split(',')[0][:32]} ({c})" for r, c in p["reasons"].most_common(2)),
            )
            for u, p in sorted(pending.items(), key=lambda kv: -kv[1]["gpus"])
        ]
        blocks.extend(
            sections(
                ":hourglass_flowing_sand: *Pending*\n"
                + table(("USER", "JOBS", "GPU", "LONGEST", "REASON"), rows)
            )
        )

    by_user = defaultdict(list)
    for j in jobs:
        if j["gpus"]:
            by_user[j["user"]].append(j)
    if by_user:
        blocks.append({"type": "divider"})
        blocks.extend(sections(":busts_in_silhouette: *Running GPU jobs by user*"))
    for user, ujobs in sorted(by_user.items(), key=lambda kv: -sum(len(j["gpus"]) for j in kv[1])):
        n = sum(len(j["gpus"]) for j in ujobs)
        rows = []
        for j in sorted(ujobs, key=lambda j: -j["runtime"]):
            hosts = ",".join(sorted({h for h, _ in j["gpus"]}))
            note = (
                "idle" if j["idle"] else (f"{j['idle_gpus']}/{len(j['gpus'])} idle" if j["idle_gpus"] else "")
            )
            rows.append(
                (
                    j["label"],
                    f"{len(j['gpus'])}x {j['gpu_type'] or 'gpu'}",
                    hosts,
                    duration(j["runtime"]),
                    j["qos"],
                    bar(j["util"]),
                    gib(j),
                    note,
                )
            )
        head = (
            f"*{user}*  {n} GPU ({100 * n / total:.1f}%)  |  {len(ujobs)} job{'s' if len(ujobs) > 1 else ''}"
        )
        blocks.extend(
            sections(f"{head}\n{table(('JOB', 'GPU', 'NODE', 'TIME', 'QOS', 'UTIL', 'MEM', ''), rows)}")
        )

    thread = [("GPU usage details", blocks[i : i + MAX_BLOCKS]) for i in range(0, len(blocks), MAX_BLOCKS)]
    return (text, summary), thread


# --- Slack ----------------------------------------------------------------------


def slack_id(user, cfg, slack, cache):
    """Slack member ID for a Slurm user (clustertools.slack: the map, else by email)."""
    return slack_member(slack, user, cfg["user_map"], cfg["email_domain"], cache)


# --- Main -----------------------------------------------------------------------

CONFIG_KEYS = {
    "slack_token": str, "channel": str, "prometheus_url": str, "idle_hours": (int, float),
    "idle_util_threshold": (int, float), "notify": list, "notify_repeat_hours": (int, float),
    "dm_text": str, "cancel_after_hours": (int, float), "user_map": dict, "email_domain": str,
    "state_file": str, "admin_channel": str, "repeat_idle_gpu_hours": (int, float),
    "repeat_idle_share": (int, float), "repeat_weeks": int, "cancel_interactive_only": bool,
    "cancel_warn_hours": (int, float), "cancel_warn_text": str, "storage_top": int,
    "storage_notify": list, "people": list,
}  # fmt: skip


class ConfigError(ValueError):
    pass


def load_config(path):
    """The config, checked whole before anything runs: a mistake in it is one line naming
    it, at deploy time (the role validates the file with --check-config) and never a
    traceback part-way through notifying owners."""
    with open(path) as f:
        try:
            cfg = json.load(f)
        except ValueError as e:
            raise ConfigError(f"{path}: not JSON: {e}") from None
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path}: not a JSON object")
    for key, kind in CONFIG_KEYS.items():
        if not isinstance(cfg.get(key), kind) or (kind is not bool and isinstance(cfg.get(key), bool)):
            raise ConfigError(f"{path}: {key} is missing or not a {getattr(kind, '__name__', 'number')}")
    for key in ("notify", "storage_notify"):
        if set(cfg[key]) - NOTIFY:
            raise ConfigError(f"{path}: {key} takes only {', '.join(sorted(NOTIFY))}")
    if cfg["storage_top"] < 1:
        raise ConfigError(f"{path}: storage_top must be 1 or more")
    if cfg["idle_hours"] <= 0:
        raise ConfigError(f"{path}: idle_hours must be above 0")
    try:
        cfg["dm_text"].format(**DM_FIELDS)
    except (KeyError, IndexError, ValueError) as e:
        raise ConfigError(f"{path}: dm_text: {e!r}; it may use only {{{'}, {'.join(DM_FIELDS)}}}") from None
    if cfg["cancel_after_hours"] > 0:
        # A job is cancelled only after its owner was told in an earlier run, so the
        # policy needs a notification and room between turning idle and being cancelled.
        if not cfg["notify"]:
            raise ConfigError(f"{path}: cancel_after_hours needs notify, so owners are told first")
        if cfg["cancel_after_hours"] <= cfg["idle_hours"]:
            raise ConfigError(f"{path}: cancel_after_hours must exceed idle_hours")
        if not 0 <= cfg["cancel_warn_hours"] < cfg["cancel_after_hours"] - cfg["idle_hours"]:
            raise ConfigError(f"{path}: cancel_warn_hours must be under cancel_after_hours minus idle_hours")
        try:
            cfg["cancel_warn_text"].format(**DM_FIELDS)
        except (KeyError, IndexError, ValueError) as e:
            raise ConfigError(
                f"{path}: cancel_warn_text: {e!r}; it may use only {{{'}, {'.join(DM_FIELDS)}}}"
            ) from None
    return cfg


def load_state(path):
    """What earlier runs recorded, {"idle": {job: {since, notified}}, "users": {...}}.
    A missing or damaged file, or a damaged entry, is a fresh start for what it held."""
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    state = state if isinstance(state, dict) else {}
    idle, users = state.get("idle"), state.get("users")
    number = (int, float)
    return {
        "idle": {
            k: v
            for k, v in (idle.items() if isinstance(idle, dict) else ())
            if isinstance(v, dict)
            and {"since", "notified"} <= set(v)
            and isinstance(v["since"], number)
            and not isinstance(v["since"], bool)
            and (
                v["notified"] is None
                or (isinstance(v["notified"], number) and not isinstance(v["notified"], bool))
            )
        },
        "users": {
            k: v
            for k, v in (users.items() if isinstance(users, dict) else ())
            if isinstance(v, dict) and isinstance(v.get("checked"), number) and isinstance(v.get("id"), str)
        },
    }


def report_once():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="/etc/slurm-usage-report/config.json")
    ap.add_argument("--dry-run", action="store_true", help="print instead of posting")
    ap.add_argument(
        "--to",
        metavar="CHANNEL_OR_MEMBER",
        help="post a preview there instead; notifies, cancels and records nothing",
    )
    ap.add_argument("--check-config", action="store_true", help="check the config and exit")
    other = ap.add_mutually_exclusive_group()
    other.add_argument("--weekly", action="store_true", help="post the weekly per-person summary instead")
    other.add_argument("--storage", action="store_true", help="post the daily disk usage report instead")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.check_config:
        return
    if args.weekly:
        weekly(cfg, args)
        return
    if args.storage:
        storage(cfg, args)
        return
    preview = args.dry_run or bool(args.to)
    state = load_state(cfg["state_file"])

    now = time.time()
    window = int(cfg["idle_hours"] * 3600)
    slack = Slack(cfg["slack_token"], args.dry_run)
    report = collect_slurm(now)
    gpu = collect_gpus(Prometheus(cfg["prometheus_url"]), window)
    report.update(
        now=now,
        dashboard_url=site_info().get("dashboard_url", ""),
        total_gpus=sum(report["nodes"].values()),
        idle=analyze(report["jobs"], gpu, window, cfg["idle_util_threshold"]),
    )
    track_idle(
        report["idle"],
        state.setdefault("idle", {}),
        now,
        window,
        cfg["notify_repeat_hours"] * 3600,
        [] if preview else cfg["notify"],
    )
    cache = state.setdefault("users", {})

    who = namer(cfg, slack, cache)

    if preview:  # a preview notifies, cancels and records nothing
        post_report(report, cfg, who, slack, args.to)
        return
    # From here the state is saved whatever fails: when each job turned idle and whom
    # this run notified, so a failure is not followed by notifying everyone again.
    try:
        act(report, cfg, who, slack, cache, now)
    finally:
        write_atomic(cfg["state_file"], json.dumps(state), mode=0o600)


def namer(cfg, slack, cache):
    """who(user, mention): the name a report shows, a Slack mention when asked and the
    member is known. A failed lookup leaves the name unmentioned rather than the report
    unposted; act() looks the member up again and reports the failure with the others."""

    def who(user, mention):
        try:
            member = slack_id(user, cfg, slack, cache) if mention else ""
        except (RuntimeError, OSError):
            member = ""
        return f"<@{member}> ({user})" if member else user

    return who


def remember_week(weeks, rows, now):
    """Add this week's numbers to each person's history (WEEKS_KEPT weeks); people with no
    GPU jobs this week get a zero week, so a streak needs consecutive weeks. A run within
    six days of the last (run again by hand) replaces that week instead of adding one."""
    seen = {r["user"]: r for r in rows}
    for user in set(weeks) | set(seen):
        r = seen.get(user, {"held": 0.0, "idle": 0.0})
        entry = {"end": int(now), "held": r["held"], "idle": r["idle"]}
        past = weeks.get(user, [])
        if past and now - past[-1]["end"] < 6 * 86400:
            entry["end"] = past[-1]["end"]  # the week keeps the time it was first recorded
            past = past[:-1]
        weeks[user] = (past + [entry])[-WEEKS_KEPT:]
        if not any(w["held"] for w in weeks[user]):
            del weeks[user]


def repeaters(weeks, cfg):
    """The people over both idle thresholds in each of the last repeat_weeks weeks."""
    n = cfg["repeat_weeks"]

    def heavy(w):
        share = w["idle"] / w["held"] if w["held"] else 0
        return w["idle"] >= cfg["repeat_idle_gpu_hours"] and share >= cfg["repeat_idle_share"]

    return sorted(u for u, ws in weeks.items() if n > 0 and len(ws) >= n and all(heavy(w) for w in ws[-n:]))


def render_repeaters(users, weeks, jobs, cfg):
    """(text, blocks) for the administrators: who, each week's numbers, their jobs now,
    and the steps to take."""
    head = f"Heavy idle GPU use {cfg['repeat_weeks']} weeks running: {', '.join(users)}"
    parts = []
    for u in users:
        hist = ", ".join(f"{w['idle']:,.0f} of {w['held']:,.0f} h" for w in weeks[u][-cfg["repeat_weeks"] :])
        running = ", ".join(f"`{j['label']}` ({len(j['gpus'])} GPU)" for j in jobs if j["user"] == u)
        parts.append(f"*{u}*: idle GPU-hours by week {hist}. Now: {running or 'no jobs'}.")
    steps = (
        "What to do: 1) message them with these numbers and ask what the jobs wait on; "
        "2) check their jobs (`sq`, the Grafana job panels) for GPUs held while the work runs on CPU or "
        "waits for input, and suggest splitting CPU and GPU stages (--dependency) or fewer GPUs; "
        "3) if it goes on, agree a limit with them (their QoS) or cancel jobs that hold GPUs idle. "
        f"Thresholds: {cfg['repeat_idle_gpu_hours']:g} idle GPU-hours and {cfg['repeat_idle_share']:.0%} "
        "of the hours held, each week."
    )
    blocks = sections("\n".join(parts)) + [
        {"type": "context", "elements": [{"type": "mrkdwn", "text": steps}]}
    ]
    return head, blocks


def weekly(cfg, args):
    """The weekly summary, and the administrators' notice of heavy idlers. Touches no
    job; a preview posts both to --to and records nothing. Its own state file (the weeks
    and member lookups), so it never races the hourly report's."""
    slack = Slack(cfg["slack_token"], args.dry_run)
    now = time.time()
    rows = collect_week(Prometheus(cfg["prometheus_url"]), cfg["idle_util_threshold"])
    path, state = side_state(cfg, "weekly.json")
    weeks, cache = state.get("weeks", {}), state.get("users", {})
    who = namer(cfg, slack, cache)
    preview = args.dry_run or bool(args.to)
    text, blocks = render_week(rows, who)
    slack.post(slack.channel(args.to) if args.to else cfg["channel"], text, blocks)
    remember_week(weeks, rows, now)
    heavy = repeaters(weeks, cfg)
    if heavy and (cfg["admin_channel"] or args.to):
        text, blocks = render_repeaters(heavy, weeks, collect_slurm(now)["jobs"], cfg)
        slack.post(slack.channel(args.to) if args.to else cfg["admin_channel"], text, blocks)
    if not preview:
        write_atomic(path, json.dumps({"weeks": weeks, "users": cache}), mode=0o600)


def side_state(cfg, name):
    """(path, {key: dict}) of a run's own state file beside the hourly one's (weekly.json,
    storage.json), so runs on different timers never race over one file; a missing or
    damaged file, or a damaged entry, is a fresh start for what it held."""
    path = os.path.join(os.path.dirname(cfg["state_file"]), name)
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    state = state if isinstance(state, dict) else {}
    return path, {k: v for k, v in state.items() if isinstance(v, dict)}


def post_thread(slack, channel, card, thread):
    """Post the card (text, blocks) and its thread [(text, blocks)]; the card's ts."""
    ts = slack.post(channel, *card)
    for t, blocks in thread:
        slack.post(channel, t, blocks, thread_ts=ts)
    return ts


def storage(cfg, args):
    """The daily disk usage report; with dm in storage_notify, a note to everyone past a
    level. A preview posts to --to and sends and records nothing; one failed note does not
    stop the others, and the run fails at the end naming them."""
    data = storage_report.collect(Prometheus(cfg["prometheus_url"]), time.time())
    if data is None:
        print("slurm-usage-report: no disk usage is counted anywhere (roles/disk_quota); nothing to post")
        return
    slack = Slack(cfg["slack_token"], args.dry_run)
    path, state = side_state(cfg, "storage.json")
    cache = state.get("users", {})
    preview = args.dry_run or bool(args.to)
    who = namer(cfg, slack, cache) if "mention" in cfg["storage_notify"] else (lambda user, mention: user)
    card, thread = storage_report.render(data, who, cfg["storage_top"], set(cfg["people"]))
    post_thread(slack, slack.channel(args.to) if args.to else cfg["channel"], card, thread)
    failures = []
    if "dm" in cfg["storage_notify"] and not preview:
        for row in storage_report.over_warn(data):
            try:
                member = slack_id(row[2], cfg, slack, cache)
                if member:
                    slack.post(slack.channel(member), storage_report.dm_text(row))
            except (RuntimeError, OSError) as e:
                failures.append(f"notifying {row[2]}: {e}")
    if not preview:
        write_atomic(path, json.dumps({"users": cache}), mode=0o600)
    if failures:
        raise RuntimeError("; ".join(failures))


def post_report(report, cfg, who, slack, to):
    """The summary card and its thread; returns (channel, ts) of the card."""
    card, thread = render(report, cfg, who)
    channel = slack.channel(to) if to else cfg["channel"]
    return channel, post_thread(slack, channel, card, thread)


def cancellable(idle, cfg):
    """The idle jobs the policy may cancel: interactive sessions only, unless configured
    otherwise (a batch job ends by itself; a shell holds GPUs until someone exits)."""
    return [j for j in idle if j.get("interactive") or not cfg["cancel_interactive_only"]]


def act(report, cfg, who, slack, cache, now):
    """Post the report, notify the owners of idle jobs and cancel those idle too long.
    One failed notification or cancellation does not stop the rest; the run still
    fails at the end, naming them."""
    channel, ts = post_report(report, cfg, who, slack, None)
    failures = []
    for j in report["idle"]:
        if not j["notify"]:
            continue
        # Notified means a message reached the owner: the report's mention (posted above,
        # which needs their Slack member) or a DM. Only then may cancelling follow.
        try:
            member = slack_id(j["user"], cfg, slack, cache)
        except (RuntimeError, OSError) as e:
            failures.append(f"looking up {j['user']} for {j['label']}: {e}")
            continue
        if not member:
            print(f"slurm-usage-report: {j['user']} has no Slack member; {j['label']} not notified",
                  file=sys.stderr)  # fmt: skip
            continue  # so never cancelled either
        delivered = "mention" in cfg["notify"]
        if "dm" in cfg["notify"]:
            try:
                slack.post(
                    slack.channel(member),
                    cfg["dm_text"].format(
                        job=j["label"],
                        gpus=len(j["gpus"]),
                        hours=f"{cfg['idle_hours']:g}",
                        runtime=duration(j["runtime"]),
                    ),
                )
                delivered = True
            except (RuntimeError, OSError) as e:
                failures.append(f"notifying {j['user']} of {j['label']}: {e}")
        if delivered:
            j["state"]["notified"] = now

    if cfg["cancel_after_hours"] > 0:
        cancelled = []
        limit, warn = cfg["cancel_after_hours"] * 3600, cfg["cancel_warn_hours"] * 3600
        for j in cancellable(report["idle"], cfg):
            idle = now - j["state"]["since"]
            # The last word before a cancel: a DM cancel_warn_hours ahead, once.
            if warn and idle >= limit - warn and not j["state"].get("warned"):
                try:
                    member = slack_id(j["user"], cfg, slack, cache)
                    if member:
                        slack.post(slack.channel(member), cfg["cancel_warn_text"].format(
                            job=j["label"], gpus=len(j["gpus"]), hours=f"{cfg['cancel_warn_hours']:g}",
                            runtime=duration(j["runtime"])))  # fmt: skip
                        j["state"]["warned"] = now
                except (RuntimeError, OSError) as e:
                    failures.append(f"warning {j['user']} of {j['label']}: {e}")
            # Only once the owner was told, and warned, in an earlier run: a lost state
            # file or a failed message never turns into a cancel without warning.
            told = j["state"]["notified"]
            warned = j["state"].get("warned") if warn else told
            if told is None or told >= now or warned is None or warned >= now or idle < limit:
                continue
            try:
                run("scancel", j["id"])
                cancelled.append(j)
            except Failed as e:
                failures.append(f"scancel {j['id']}: {e}")
        if cancelled:
            slack.post(
                channel,
                "Idle jobs cancelled",
                sections(
                    "\n".join(
                        f":no_entry: {who(j['user'], bool(cfg['notify']))}  `{j['label']}` was cancelled after "
                        f"{cfg['cancel_after_hours']:g}h with idle GPUs"
                        for j in cancelled
                    )
                ),
                thread_ts=ts,
            )
    if failures:
        raise RuntimeError("; ".join(failures))


def main():
    try:
        report_once()
    except (Error, RuntimeError, OSError, ValueError) as e:
        print(f"slurm-usage-report: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
