"""Post a Slurm GPU usage report to Slack and notify the owners of jobs
leaving their GPUs idle.

Reads jobs and node state from Slurm's JSON output, per-GPU utilisation from
Prometheus (dcgm-exporter). Posts a summary card with the detail in its thread, and
tells each person whose jobs' GPUs all stayed idle for the whole window, once for all
such jobs (mentioned, or sent a DM), again every notify_repeat_hours while any stays
idle (cluster_tools.notices). Can optionally cancel such jobs once idle for longer,
any job, each only after its owner was told and warned in earlier runs.

With --weekly it posts the week instead: for each person, the GPU-hours their jobs held
and how many of them sat idle, the people who idled most mentioned, from the per-job
GPU usage Prometheus keeps (job_device:dcgm_gpu_util_ratio:max). Seeing it is the
point: no job is touched. Someone who idled heavily (repeat_idle_gpu_hours and
repeat_idle_share) for repeat_weeks weeks in a row is reported to the administrators'
channel with the weeks' numbers, their jobs now and what to do.

With --disk it posts the day's disk usage instead (cluster_tools.disk_report, from
the disk quota metrics): every counted filesystem, its largest people (config people,
the roster's) and projects, and everyone past a warn level, mentioned or sent a DM
(disk_notify); a filesystem at least disk_fill full names its disk_fill_mentions largest
people, mentioned at most every disk_repeat_days; and the folders worth cleaning
(unused past stale_days, cluster_tools.cleanup).

With --io it tells the users of the jobs doing most I/O on a node whose NFS traffic to
an export stayed above io_share of what the disk behind it serves for io_minutes
(cluster_tools.io_report), in a DM with io_advice, again every io_repeat_hours while it
lasts, and the administrators in one line per node. Standard library only.
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

from . import cards, config, disk_report, io_report, notices, rich, rules
from .config import ConfigError
from .files import write_atomic
from .prometheus import Prometheus
from .render import duration
from .site import info as site_info
from .slack import MEMBER, DmLog, Slack, bar, escape, mentioning, pages, sections, table
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

# The one name, in every text this tool sends, for a job whose allocated GPUs all sat
# idle the whole window (the rule is in the text beside it): a noun, in titles and
# sentences alike (owner, 2026-11-27); tests/slack-text/test_terms.py holds it to the
# glossary. Named once in cluster_tools.rules, whose rule of it usage shows.
IDLE_JOB = rules.IDLE_JOB
# A drained or down node: in sentences what it means, in a title its noun.
DOWN_TITLE = "중단 노드"
NOTIFY = {"mention", "dm"}
# The fields a DM text may use: the person's jobs, one line each, how many, the hours.
DM_FIELDS = {"jobs": "- `1234_5` (GPU 8장, 1:00:00 실행 중)", "count": 1, "hours": "2"}
# An hourly run starts a few seconds later some hours: a notice due every N hours may
# go this much early, so it never slips to the next run.
RUN_LEEWAY = 300
WEEK_DAYS = 7
WEEK_TOP = 5  # the idlest people mentioned in the weekly summary
WEEKS_KEPT = 8  # weeks of each person's numbers the weekly summary remembers


# --- Slurm ----------------------------------------------------------------------
# Reading Slurm is cluster_tools.slurm's job (the cluster_tools_lib role): one reader for
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


def util_at_most(threshold):
    """The idle threshold (idle_util_threshold, percent) as a phrase: "0%" (nothing is
    below it), else "5% 이하". The hourly rule, the weekly hours and usage say it alike."""
    return "0%" if threshold == 0 else f"{threshold:g}% 이하"


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


def render_week(rows, who, threshold, days=WEEK_DAYS, top=WEEK_TOP, now=None):
    """(text, blocks): totals, then everyone who held GPUs, idlest first; the TOP idlest
    with idle hours are mentioned. THRESHOLD is collect_week's, in percent; NOW dates the
    header (default: now)."""
    held = sum(r["held"] for r in rows)
    idle = sum(r["idle"] for r in rows)
    share = idle / held if held else 0
    head = f"지난 {days}일 GPU 사용: {held:,.0f} GPU시간 중 GPU 유휴 시간 {idle:,.0f} ({share:.0%})"
    lines = []
    for n, r in enumerate(rows):
        name = who(r["user"], n < top and r["idle"] >= 1)
        part = r["idle"] / r["held"] if r["held"] else 0
        lines.append(f"- {name}: {r['held']:,.0f} GPU시간 중 유휴 {r['idle']:,.0f} ({part:.0%})")
    stamp = datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y-%m-%d")
    note = (
        f"GPU 유휴 시간: 잡이 할당받은 GPU의 사용률이 {util_at_most(threshold)}였던 시간입니다 (1분 단위로 셉니다). "
        f"GPU 유휴 시간이 가장 긴 {top}명(1시간 이상)을 멘션합니다."
    )
    return head, [
        cards.title(f"지난 {days}일 GPU 사용", stamp),
        cards.facts(
            [("할당된 GPU시간", f"{held:,.0f}", ""), ("GPU 유휴 시간", f"{idle:,.0f} ({share:.0%})", "")]
        ),
        *cards.part("사람별", "\n".join(lines) or "GPU 잡이 없었습니다."),
        cards.note(note),
        *cards.step(f"쓰지 않는 GPU는 `scancel <JOBID>`로 반납해 주세요. {rules.check_note('gpu-idle')}"),
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


def track_idle(idle, state, now, window):
    """Remember when each job turned idle (a job no longer idle is forgotten); each idle
    job gets its record as j["state"]: since, notified (its owner told of it), warned, and
    seen (the last run that found it idle). A record no run has seen for a whole window
    is started afresh: runs that failed in between (Slurm or Prometheus down) cannot tell
    whether the job was busy meanwhile, and its old since and warning would cancel a job
    idle again only now, without a fresh warning."""
    ids = {j["id"] for j in idle}
    for jid in [k for k in state if k not in ids]:
        del state[jid]
    for j in idle:
        record = state.get(j["id"])
        if record is None or now - record.get("seen", float("-inf")) > window:
            record = state[j["id"]] = {"since": now - window, "notified": None}
        record["seen"] = now
        j["state"] = record


def by_owner(jobs):
    """{user: [their jobs]}, users and jobs in a stable order (most GPUs first)."""
    out = defaultdict(list)
    for j in sorted(jobs, key=lambda j: (j["user"], -len(j["gpus"]), j["label"])):
        out[j["user"]].append(j)
    return dict(out)


def job_lines(jobs):
    """One line per job for a person's notice."""
    return "\n".join(
        f"- `{escape(j['label'])}` (GPU {len(j['gpus'])}장, {duration(j['runtime'])} 실행 중)" for j in jobs
    )


# --- Rendering ------------------------------------------------------------------


def gib(job):
    """GPU memory in use: '-' without data, a trailing '+' when some GPUs lack it."""
    if job["mem_gib"] is None:
        return "-"
    return f"{job['mem_gib']:.0f}G" + ("+" if job["mem_known"] < len(job["gpus"]) else "")


def render(report, cfg, who):
    """Summary (text, blocks) and the thread messages [(text, blocks)]. Each section is
    its own function of only what it shows, so no section can see another's names."""
    blocks = [
        *idle_section(report["idle"], cfg, report.get("due", ()), who),
        *nodes_section(report["reasons"]),
        *pending_section(report["pending"]),
        *people_section(report["jobs"], max(report["total_gpus"], 1)),
    ]
    thread = pages("GPU 사용 현황 자세히", blocks)
    return summary_card(report), thread


def summary_card(report):
    """The channel message: (text, blocks)."""
    jobs, idle, total = report["jobs"], report["idle"], max(report["total_gpus"], 1)
    alloc = sum(len(j["gpus"]) for j in jobs)
    utils = [j["util"] for j in jobs if j["util"] is not None for _ in j["gpus"]]
    # Unknown is not zero: with no utilisation data (no jobs, or Prometheus down) say so.
    avg = f"{sum(utils) / len(utils):.0f}%" if utils else "알 수 없음"
    pending = report["pending"]
    pend_jobs = sum(p["jobs"] for p in pending.values())
    pend_gpus = sum(p["gpus"] for p in pending.values())
    stamp = datetime.fromtimestamp(report["now"]).strftime("%Y-%m-%d %H:%M")

    text = (
        f"GPU 사용 현황 {stamp}: GPU {alloc}/{total}장 할당 ({100 * alloc / total:.0f}%), "
        f"평균 사용률 {avg}, {IDLE_JOB} {len(idle)}개"
    )
    facts = [
        ("할당된 GPU", f"{alloc} / {total}장 ({100 * alloc / total:.0f}%)", ""),
        ("평균 사용률", avg, ""),
        (IDLE_JOB, f"{len(idle)}개", "warn" if idle else "ok"),
        ("대기 중인 잡", f"{pend_jobs}개, GPU {pend_gpus}장", ""),
        (f"{DOWN_TITLE} (drain, down)", f"{len(report['reasons'])}대", "warn" if report["reasons"] else "ok"),
    ]
    more = "자세한 내용은 이 메시지의 스레드에 있습니다."
    if report.get("dashboard_url"):
        more += f" <{report['dashboard_url']}|Grafana>"
    return text, [
        cards.title("GPU 사용 현황", stamp),
        cards.facts(facts),
        cards.note(more),
        *cards.step(f"내 잡의 GPU 사용은 `{rules.BY_ID['gpu-idle'].check}`로 봅니다."),
    ]


def idle_section(idle, cfg, due, who):
    """The jobs leaving their GPUs idle, per owner, each owner mentioned when told this run (DUE)."""
    if not idle:
        return []
    rule = f"모든 GPU의 사용률이 {util_at_most(cfg['idle_util_threshold'])}로 {cfg['idle_hours']:g}시간 넘게 이어진 잡"
    lines = [f"_{rule}_"]
    owners = by_owner(idle)
    for user in sorted(owners, key=lambda u: (-sum(len(j["gpus"]) for j in owners[u]), u)):
        told = "mention" in cfg["notify"] and user in due
        lines.append(f"- {who(user, told)}  잡 {len(owners[user])}개")
        for j in owners[user]:
            hosts = ",".join(sorted({h for h, _ in j["gpus"]}))
            lines.append(
                f"  - `{j['label']}`  {j['gpu_type'] or 'GPU'} {len(j['gpus'])}장, {hosts}  "
                f"{duration(j['runtime'])} 실행 중"
            )
    return cards.part(IDLE_JOB, "\n".join(lines), "warn") + cards.step(
        "더 쓰지 않으신다면 `scancel <JOBID>`로 GPU를 반납해 주세요."
    )


def nodes_section(reasons):
    """The nodes that cannot run jobs (drain, down), with why."""
    if not reasons:
        return []
    rows = [(n, st, since.replace("T", " ")[:16], u, why) for n, st, since, u, why in reasons]
    down = table(("노드", "상태", "언제부터", "누가", "사유"), rows)
    return cards.part(f"{DOWN_TITLE} (drain, down)", down, "warn")


def pending_section(pending):
    """The waiting jobs per person: how many, how many GPUs, the longest wait, why."""
    if not pending:
        return []
    rows = [
        (
            u,
            p["jobs"],
            p["gpus"],
            duration(p["wait"]),
            ", ".join(f"{r.split(',')[0][:32]} ({c})" for r, c in p["reasons"].most_common()),
        )
        for u, p in sorted(pending.items(), key=lambda kv: -kv[1]["gpus"])
    ]
    return cards.part("대기 중인 잡", table(("사용자", "잡", "GPU", "최장 대기", "사유"), rows))


def people_section(jobs, total):
    """Every running GPU job, per person, the most GPUs first; idle ones marked."""
    by_user = defaultdict(list)
    for j in jobs:
        if j["gpus"]:
            by_user[j["user"]].append(j)
    if not by_user:
        return []
    blocks = [{"type": "divider"}, *cards.part("사람별 실행 중인 GPU 잡")]
    for user, ujobs in sorted(by_user.items(), key=lambda kv: -sum(len(j["gpus"]) for j in kv[1])):
        n = sum(len(j["gpus"]) for j in ujobs)
        rows = []
        for j in sorted(ujobs, key=lambda j: -j["runtime"]):
            hosts = ",".join(sorted({h for h, _ in j["gpus"]}))
            note = (
                "유휴" if j["idle"] else (f"{j['idle_gpus']}/{len(j['gpus'])} 유휴" if j["idle_gpus"] else "")
            )
            rows.append(
                (
                    j["label"],
                    f"{j['gpu_type'] or 'GPU'} {len(j['gpus'])}장",
                    hosts,
                    duration(j["runtime"]),
                    j["qos"],
                    bar(j["util"]),
                    gib(j),
                    note,
                )
            )
        blocks += cards.part(
            f"{user}  GPU {n}장 ({100 * n / total:.1f}%), 잡 {len(ujobs)}개",
            table(("잡", "GPU", "노드", "시간", "QoS", "사용률", "메모리", ""), rows),
        )
    return blocks


# --- Slack ----------------------------------------------------------------------


def slack_id(user, cfg, slack, cache):
    """Slack member ID for a Slurm user (cluster_tools.slack: the map, else by email)."""
    return slack_member(slack, user, cfg["user_map"], cfg["email_domain"], cache)


def client(cfg, args):
    """The Slack client of a run: its DMs logged for the administrators in a daily thread
    of dm_log_channel (cluster_tools.slack.DmLog), whose state sits beside the hourly
    run's, shared by every run of this tool; no channel, not logged."""
    dm_log = None
    if cfg["dm_log_channel"]:
        dm_log = DmLog(cfg["dm_log_channel"], os.path.join(os.path.dirname(cfg["state_file"]), "dm-log.json"))
    return Slack(cfg["slack_token"], args.dry_run, dm_log=dm_log)


def preview_channel(slack, to):
    """Where a --to preview goes: an operator's DM (a member ID, not logged), or a channel
    as named; "" for no preview."""
    if not to:
        return ""
    return slack.dm_channel(to) if MEMBER.fullmatch(to) else to


def to_channel(slack, to, channel, text, blocks=None):
    """Post TEXT to the --to preview target, else to CHANNEL."""
    slack.post(preview_channel(slack, to) or channel, text, blocks)


# --- Main -----------------------------------------------------------------------

CONFIG_KEYS = {
    "slack_token": str, "channel": str, "prometheus_url": str, "idle_hours": (int, float),
    "idle_util_threshold": (int, float), "notify": list, "notify_repeat_hours": (int, float),
    "dm_text": str, "cancel_after_hours": (int, float), "user_map": dict, "email_domain": str,
    "state_file": str, "admin_channel": str, "admin_mention": str, "repeat_idle_gpu_hours": (int, float),
    "repeat_idle_share": (int, float), "repeat_weeks": int,
    "cancel_warn_hours": (int, float), "cancel_warn_text": str,
    "disk_notify": list, "disk_repeat_days": (int, float), "people": list,
    "holds_on": bool, "holds_from": (int, float),
    "disk_fill": (int, float), "disk_fill_mentions": int, "stale_days": int,
}  # fmt: skip
# Keys added after configs were written: optional, with these defaults, so the package
# (which every host gets first) never refuses a config its role has not rewritten yet.
# No io_mounts, no I/O notices.
# No dm_log_channel: the DMs are not logged for the administrators (cluster_tools.slack).
# disk_top is retired (the disk report lists every person): accepted, read by nothing,
# until every config is rewritten by its role.
CONFIG_DEFAULTS = {"io_mounts": {}, "io_share": 0.4, "io_minutes": 15, "io_top_jobs": 3, "io_repeat_hours": 6,
                   "io_advice": "", "dm_log_channel": ""}  # fmt: skip
CONFIG_OPTIONAL = {"io_mounts": dict, "io_share": (int, float), "io_minutes": int, "io_top_jobs": int,
                   "io_repeat_hours": (int, float), "io_advice": str, "dm_log_channel": str,
                   "disk_top": int}  # fmt: skip


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def load_config(path):
    """The config, checked whole before anything runs: a mistake in it is one line naming
    it, at deploy time (the role validates the file, cluster_tools.config) and never a
    traceback part-way through notifying owners."""
    cfg = {**CONFIG_DEFAULTS, **config.require(path, CONFIG_KEYS, CONFIG_OPTIONAL)}
    for key in ("notify", "disk_notify"):
        if set(cfg[key]) - NOTIFY:
            raise ConfigError(f"{path}: {key} takes only {', '.join(sorted(NOTIFY))}")
    if not 0 < cfg["disk_fill"] <= 1:
        raise ConfigError(f"{path}: disk_fill is a fraction above 0, at most 1")
    if cfg["disk_repeat_days"] < 0:
        raise ConfigError(f"{path}: disk_repeat_days must be 0 (every day) or more")
    if cfg["idle_hours"] <= 0:
        raise ConfigError(f"{path}: idle_hours must be above 0")
    if not 0 < cfg["io_share"] <= 1:
        raise ConfigError(f"{path}: io_share is a fraction above 0, at most 1")
    if cfg["io_minutes"] < 5 or cfg["io_top_jobs"] < 1 or cfg["io_repeat_hours"] < 0:
        raise ConfigError(f"{path}: io_minutes must be 5 or more (the node rates are 5-minute rates), "
                          "io_top_jobs 1 or more and io_repeat_hours 0 or more")  # fmt: skip
    for mount, capacity in cfg["io_mounts"].items():
        if (not isinstance(capacity, dict) or set(capacity) != {"read_bps", "write_bps"}
                or not all(_positive(v) for v in capacity.values())):  # fmt: skip
            raise ConfigError(f"{path}: io_mounts {mount}: needs read_bps and write_bps above 0")
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
    """What earlier runs recorded, {"idle": {job: {since, notified}}, "told": {user: when
    they were last told}, "users": {...}}. A missing or damaged file, or a damaged entry,
    is a fresh start for what it held."""
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    state = state if isinstance(state, dict) else {}
    idle, users, told = state.get("idle"), state.get("users"), state.get("told")
    number = (int, float)
    told = told if isinstance(told, dict) else {}
    return {
        "told": {k: v for k, v in told.items() if notices.told_at(told, k) is not None},
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
    ap.add_argument("--config", default="/etc/cluster-tools/slurm-usage-report.json")
    ap.add_argument("--dry-run", action="store_true", help="print instead of posting")
    ap.add_argument(
        "--to",
        metavar="CHANNEL_OR_MEMBER",
        help="post a preview there instead; notifies, cancels and records nothing",
    )
    other = ap.add_mutually_exclusive_group()
    other.add_argument("--weekly", action="store_true", help="post the weekly per-person summary instead")
    other.add_argument("--disk", action="store_true", help="post the daily disk usage report instead")
    other.add_argument("--io", action="store_true", help="tell who drives an NFS export's load instead")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.weekly:
        weekly(cfg, args)
        return
    if args.disk:
        disk(cfg, args)
        return
    if args.io:
        io(cfg, args)
        return
    preview = args.dry_run or bool(args.to)
    state = load_state(cfg["state_file"])

    now = time.time()
    window = int(cfg["idle_hours"] * 3600)
    slack = client(cfg, args)
    report = collect_slurm(now)
    gpu = collect_gpus(Prometheus(cfg["prometheus_url"]), window)
    report.update(
        now=now,
        dashboard_url=site_info().get("dashboard_url", ""),
        total_gpus=sum(report["nodes"].values()),
        idle=analyze(report["jobs"], gpu, window, cfg["idle_util_threshold"]),
    )
    track_idle(report["idle"], state["idle"], now, window)
    # Whom this run tells: a preview tells no one, and records nothing.
    report["due"] = (
        []
        if preview or not cfg["notify"]
        else notices.due(
            sorted(by_owner(report["idle"])), state["told"], now, cfg["notify_repeat_hours"] * 3600,
            leeway=RUN_LEEWAY,
        )
    )  # fmt: skip
    report["told"] = state["told"]
    cache = state["users"]

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
        parts.append(f"**{u}**: idle GPU-hours by week {hist}. Now: {running or 'no jobs'}.")
    steps = (
        "What to do: 1) message them with these numbers and ask what the jobs wait on; "
        "2) check their jobs (`sq`, the Grafana job panels) for GPUs held while the work runs on CPU or "
        "waits for input, and suggest splitting CPU and GPU stages (--dependency) or fewer GPUs; "
        "3) if it goes on, agree a limit with them (their QoS) or cancel jobs that hold GPUs idle. "
        f"Thresholds: {cfg['repeat_idle_gpu_hours']:g} idle GPU-hours and {cfg['repeat_idle_share']:.0%} "
        "of the hours held, each week."
    )
    blocks = sections("\n".join(parts)) + [rich.context(steps)]
    return head, blocks


def weekly(cfg, args):
    """The weekly summary, and the administrators' notice of heavy idlers. Touches no
    job; a preview posts both to --to and records nothing. Its own state file (the weeks
    and member lookups), so it never races the hourly report's."""
    slack = client(cfg, args)
    now = time.time()
    rows = collect_week(Prometheus(cfg["prometheus_url"]), cfg["idle_util_threshold"])
    path, state = side_state(cfg, "weekly.json")
    weeks, cache = state.get("weeks", {}), state.get("users", {})
    who = namer(cfg, slack, cache)
    preview = args.dry_run or bool(args.to)
    text, blocks = render_week(rows, who, cfg["idle_util_threshold"])
    to_channel(slack, args.to, cfg["channel"], text, blocks)
    remember_week(weeks, rows, now)
    heavy = repeaters(weeks, cfg)
    if heavy and (cfg["admin_channel"] or args.to):
        text, blocks = mentioning(
            cfg["admin_mention"], *render_repeaters(heavy, weeks, collect_slurm(now)["jobs"], cfg)
        )
        to_channel(slack, args.to, cfg["admin_channel"], text, blocks)
    if not preview:
        write_atomic(path, json.dumps({"weeks": weeks, "users": cache}), mode=0o600)


def side_state(cfg, name):
    """(path, {key: dict}) of a run's own state file beside the hourly one's (weekly.json,
    disk.json), so runs on different timers never race over one file; a missing or
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


def disk(cfg, args):
    """The daily disk usage report. The people past a level who are due a notice
    (cluster_tools.notices: a first crossing, a hard limit every day, the same warn level
    every disk_repeat_days) are mentioned on the card or sent a DM, as disk_notify says,
    and recorded once it reached them. A preview posts to --to and sends and records
    nothing; one failed DM does not stop the others, and the run fails at the end naming
    them."""
    now = time.time()
    data = disk_report.collect(Prometheus(cfg["prometheus_url"]), now)
    if data is None:
        print("slurm-usage-report: no disk usage is counted anywhere (roles/disk_quota); nothing to post")
        return
    slack = client(cfg, args)
    path, state = side_state(cfg, "disk.json")
    cache, told = state.get("users", {}), state.get("told", {})
    preview = args.dry_run or bool(args.to)
    notify = cfg["disk_notify"]
    rows = disk_report.over_warn(data)
    # A filesystem filling up names its largest people every day, mentioning them when
    # its notice is due: at most every disk_repeat_days, like a warn level.
    fill = disk_report.fill_rows(data, cfg["disk_fill"], set(cfg["people"]), cfg["disk_fill_mentions"])
    # One call over every key of this record: notices.due forgets the keys it is not
    # given, so a second call on the same record would erase the first's.
    keys = set(
        notices.due(
            [disk_report.row_key(r) for r in rows] + [disk_report.fill_key(k) for k, _, _ in fill],
            told, now, cfg["disk_repeat_days"] * disk_report.DAY,
            urgent={disk_report.row_key(r) for r in rows if r[4][0][3] == "hard"}, leeway=disk_report.LEEWAY,
        )
    )  # fmt: skip
    due = [r for r in rows if disk_report.row_key(r) in keys]
    fill_due = {disk_report.fill_key(k) for k, _, _ in fill} & keys
    who = namer(cfg, slack, cache)
    hold = cfg["holds_from"] if cfg["holds_on"] else None
    card, thread = disk_report.render(
        data, who, people=set(cfg["people"]), mention=keys if "mention" in notify else set(), hold=hold,
        fill=fill, fill_mention=fill_due, stale_days=cfg["stale_days"],
    )  # fmt: skip
    if not preview:
        for k in fill_due:
            told[k] = int(now)
    post_thread(slack, preview_channel(slack, args.to) or cfg["channel"], card, thread)
    failures = []
    for row in due:
        delivered = "mention" in notify
        if "dm" in notify and not preview:
            try:
                member = slack_id(row[2], cfg, slack, cache)
                if member:
                    text = disk_report.dm_text(row, disk_report.hold_note(hold, now))
                    slack.dm(member, *cards.notice("disk", text), kind="disk", login=row[2])
                    delivered = True
            except (RuntimeError, OSError) as e:
                failures.append(f"notifying {row[2]}: {e}")
        if delivered:
            told[disk_report.row_key(row)] = int(now)
    failures += purge_notices(cfg, slack, cache, state, now, preview)
    if not preview:
        write_atomic(
            path,
            json.dumps({"users": cache, "told": told, "purge_told": state.get("purge_told", {})}),
            mode=0o600,
        )
    if failures:
        raise RuntimeError("; ".join(failures))


def io(cfg, args):
    """The I/O notices. For each node whose NFS traffic to a measured export stayed hot
    (cluster_tools.io_report), the users of its top jobs are sent a DM and the
    administrators one line per node, each when due (cluster_tools.notices: at once,
    then every io_repeat_hours while it lasts); a root job is named to the
    administrators only. A preview prints to --to and records nothing; one failed DM does
    not stop the others, and the run fails at the end naming them."""
    now = time.time()
    if not cfg["io_mounts"]:
        return
    hot = io_report.collect(Prometheus(cfg["prometheus_url"]), cfg["io_mounts"], cfg["io_share"],
                            cfg["io_minutes"], cfg["io_top_jobs"])  # fmt: skip
    slack = client(cfg, args)
    path, state = side_state(cfg, "io.json")
    cache, told = state.get("users", {}), state.get("told", {})
    preview = args.dry_run or bool(args.to)
    people = [
        (item, rank, user) for item in hot for rank, (_, user, _) in enumerate(item["jobs"]) if user != "root"
    ]
    keys = set(notices.due([io_report.user_key(i, u) for i, _, u in people] + [io_report.admin_key(i) for i in hot],
                           told, now, cfg["io_repeat_hours"] * 3600, leeway=RUN_LEEWAY))  # fmt: skip
    failures = []
    if args.to:
        if hot:
            to_channel(slack, args.to, "", io_report.admin_text(hot))
        return
    for item, rank, user in people:
        key = io_report.user_key(item, user)
        if key not in keys or preview:
            continue
        try:
            member = slack_id(user, cfg, slack, cache)
            if member:
                text = io_report.dm_text(item, rank, cfg["io_advice"])
                slack.dm(member, *cards.notice("io-heavy", text), kind="io", login=user)
                told[key] = int(now)
        except (RuntimeError, OSError) as e:
            failures.append(f"notifying {user}: {e}")
    due = [item for item in hot if io_report.admin_key(item) in keys]
    if due and cfg["admin_channel"] and not preview:
        try:
            slack.post(cfg["admin_channel"], io_report.admin_text(due))
            for item in due:
                told[io_report.admin_key(item)] = int(now)
        except (RuntimeError, OSError) as e:
            failures.append(f"telling the administrators: {e}")
    if not preview:
        write_atomic(path, json.dumps({"users": cache, "told": told}), mode=0o600)
    if failures:
        raise RuntimeError("; ".join(failures))


def purge_notices(cfg, slack, cache, state, now, preview):
    """Tell each person once per removal date of the scratch cleanup (cluster_tools.
    scratch_space) in a DM, whatever disk_notify says (it is their files going, not a
    disk card), and the administrators of a mass access-time refresh once a day; the
    failures, as text."""
    purge = disk_report.collect_purge(Prometheus(cfg["prometheus_url"]))
    told = state.setdefault("purge_told", {})
    entries = {disk_report.purge_key(k, e): (k, e) for k, e in purge["due"].items()}
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    bursts = {f"burst {k[0]} {k[1]} {day}": (k, n) for k, n in purge["bursts"].items()}
    keys = notices.due(list(entries) + list(bursts), told, now, 10**9)
    failures = []
    for key in keys:
        if preview:
            continue
        try:
            if key in entries:
                k, entry = entries[key]
                member = slack_id(k[1], cfg, slack, cache)
                if member:
                    text = disk_report.purge_dm_text(k, entry)
                    slack.dm(member, *cards.notice("scratch-purge", text), kind="scratch-purge", login=k[1])
                    told[key] = int(now)
            elif key in bursts and cfg["admin_channel"]:
                k, n = bursts[key]
                slack.post(
                    cfg["admin_channel"], *mentioning(cfg["admin_mention"], disk_report.burst_text(k, n))
                )
                told[key] = int(now)
        except (RuntimeError, OSError) as e:
            failures.append(f"scratch cleanup notice {key}: {e}")
    return failures


def cancelled_note(cancelled, cfg, who):
    """The report thread's (text, blocks) on the jobs CANCELLED for leaving their GPUs idle."""
    lines = [
        f"{cards.mark('act')}{who(user, bool(cfg['notify']))}  "
        + ", ".join(f"`{j['label']}`" for j in jobs)
        + f": 할당받은 GPU를 {cfg['cancel_after_hours']:g}시간 동안 쓰지 않아 취소했습니다"
        for user, jobs in by_owner(cancelled).items()
    ]
    return f"{IDLE_JOB}을 취소했습니다", cards.part(f"{IDLE_JOB} 취소", "\n".join(lines), "act")


def post_report(report, cfg, who, slack, to):
    """The summary card and its thread; returns (channel, ts) of the card."""
    card, thread = render(report, cfg, who)
    channel = preview_channel(slack, to) or cfg["channel"]
    return channel, post_thread(slack, channel, card, thread)


def act(report, cfg, who, slack, cache, now):
    """Post the report, tell the people due a notice (report["due"]) about all their idle
    jobs at once, and cancel jobs idle too long, warning each person once ahead for all
    their jobs about to go. One failed notice or cancellation does not stop the rest; the
    run still fails at the end, naming them."""
    channel, ts = post_report(report, cfg, who, slack, None)
    failures = []
    owners = by_owner(report["idle"])
    hours = f"{cfg['idle_hours']:g}"
    for user in report["due"]:
        jobs = owners[user]
        # Told means a message reached them: the report's mention (posted above, which
        # needs their Slack member) or a DM. Only then may cancelling follow.
        try:
            member = slack_id(user, cfg, slack, cache)
        except (RuntimeError, OSError) as e:
            failures.append(f"looking up {user}: {e}")
            continue
        if not member:
            print(
                f"slurm-usage-report: {user} has no Slack member; not told of jobs leaving their GPUs idle",
                file=sys.stderr,
            )
            continue  # so never cancelled either
        delivered = "mention" in cfg["notify"]
        if "dm" in cfg["notify"]:
            try:
                text = cfg["dm_text"].format(jobs=job_lines(jobs), count=len(jobs), hours=hours)
                text = f"{text}\n{rules.check_note('gpu-idle')}"
                slack.dm(member, *cards.notice("gpu-idle", text), kind="idle", login=user)
                delivered = True
            except (RuntimeError, OSError) as e:
                failures.append(f"notifying {user}: {e}")
        if delivered:
            report["told"][user] = now
            for j in jobs:
                j["state"]["notified"] = now

    if cfg["cancel_after_hours"] > 0:
        limit, warn = cfg["cancel_after_hours"] * 3600, cfg["cancel_warn_hours"] * 3600
        # The last word before a cancel: one DM per person, cancel_warn_hours ahead, for
        # all their jobs about to go, once each.
        for user, jobs in owners.items():
            ahead = [
                j
                for j in jobs
                if warn and now - j["state"]["since"] >= limit - warn and not j["state"].get("warned")
            ]
            if not ahead:
                continue
            try:
                member = slack_id(user, cfg, slack, cache)
                if member:
                    text = cfg["cancel_warn_text"].format(
                        jobs=job_lines(ahead), count=len(ahead), hours=f"{cfg['cancel_warn_hours']:g}"
                    )
                    text = f"{text}\n{rules.check_note('gpu-idle')}"
                    slack.dm(member, *cards.notice("gpu-idle", text), kind="idle-cancel-warning", login=user)
                    for j in ahead:
                        j["state"]["warned"] = now
            except (RuntimeError, OSError) as e:
                failures.append(f"warning {user}: {e}")
        cancelled = []
        for j in report["idle"]:
            # Only once the owner was told, and warned, in an earlier run: a lost state
            # file or a failed message never turns into a cancel without warning.
            told = j["state"]["notified"]
            warned = j["state"].get("warned") if warn else told
            if (
                told is None
                or told >= now
                or warned is None
                or warned >= now
                or now - j["state"]["since"] < limit
            ):
                continue
            try:
                run("scancel", j["id"])
                cancelled.append(j)
            except Failed as e:
                failures.append(f"scancel {j['id']}: {e}")
        if cancelled:
            slack.post(channel, *cancelled_note(cancelled, cfg, who), thread_ts=ts)
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
