"""Post a Slurm GPU usage report to Slack and notify owners of idle GPU jobs.

Reads jobs and node state from Slurm's JSON output, per-GPU utilisation from
Prometheus (dcgm-exporter). Posts a summary card with the detail in its thread, and
mentions (or DMs) the owner of each job whose GPUs all stayed idle for the whole
window. Can optionally cancel jobs idle for longer. Standard library only.
"""

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime

from .files import write_atomic
from .render import duration
from .site import info as site_info
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

SLACK_API = "https://slack.com/api/"
MAX_BLOCKS = 45  # Slack allows 50 blocks per message
MAX_SECTION = 2900  # and 3000 characters per section text
NOTIFY = {"mention", "dm"}
DM_FIELDS = {"job": "1234_5", "gpus": 8, "hours": "2", "runtime": "1:00:00"}
BAR_WIDTH = 10


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


class Prometheus:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def query(self, expr):
        req = f"{self.url}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.load(resp)
        if body.get("status") != "success":
            raise RuntimeError(f"Prometheus query failed: {expr}: {body}")
        return body["data"]["result"]

    def per_gpu(self, expr):
        """{(host, Slurm GPU index): value} for a per-GPU DCGM expression.

        Slurm numbers GPUs by device minor number (/dev/nvidiaN); dcgm-exporter's gpu and
        device labels are both the NVML index, in PCI order, which can differ. Keyed on
        either, a busy job could read another GPU's idleness and be cancelled. So the
        expression is relabelled through host_gpu:dcgm_slurm_device:info (rules/slurm.yml),
        made from the minor number DCGM reports: one mapping for the alerts, the
        dashboard and this report. A GPU the mapping lacks has no data, never idle."""
        expr = f"max by (host, gpu) ({expr}) * on (host, gpu) group_left (device) host_gpu:dcgm_slurm_device:info"
        out = {}
        for r in self.query(expr):
            m = r["metric"]
            dev = re.fullmatch(r"nvidia(\d+)", m.get("device", ""))
            if not dev:
                continue  # not a GPU series: no index Slurm would know
            host = m.get("host") or m.get("instance", "").rsplit(":", 1)[0]
            out[(host, int(dev.group(1)))] = float(r["value"][1])
        return out


def collect_gpus(prom, window):
    return {
        "util_max": prom.per_gpu(f"max_over_time(DCGM_FI_DEV_GPU_UTIL[{window}s])"),
        "util": prom.per_gpu("avg_over_time(DCGM_FI_DEV_GPU_UTIL[10m])"),
        "mem_gib": prom.per_gpu("DCGM_FI_DEV_FB_USED / 1024"),
    }


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


def bar(pct):
    if pct is None:
        return "n/a".ljust(BAR_WIDTH + 5)
    filled = round(max(0.0, min(pct, 100.0)) / 100 * BAR_WIDTH)
    return "█" * filled + "░" * (BAR_WIDTH - filled) + f" {pct:3.0f}%"


def gib(job):
    """GPU memory in use: 'n/a' without data, a trailing '+' when some GPUs lack it."""
    if job["mem_gib"] is None:
        return "n/a"
    return f"{job['mem_gib']:.0f}G" + ("+" if job["mem_known"] < len(job["gpus"]) else "")


def table(header, rows):
    """A fixed-width table in a code block; Slack renders it monospaced."""
    widths = [max(len(str(c)) for c in col) for col in zip(header, *rows)]

    def fmt(r):
        return "  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip()

    return "```\n" + "\n".join([fmt(header)] + [fmt(r) for r in rows]) + "\n```"


def sections(text):
    """Slack sections holding all of TEXT. Slack rejects a section over 3000 characters,
    failing the whole post, so a longer text is split at lines into several, a code block
    closed at the end of one and reopened in the next. Nothing is cut: a line left out
    could be the mention that tells an owner about their idle job."""
    chunks, cur, fenced = [], [], False
    width = MAX_SECTION - 8  # room for a code fence closed and reopened around a piece
    pieces = [ln[i : i + width] for ln in text.split("\n") for i in range(0, max(len(ln), 1), width)]
    for line in pieces:
        if cur and len("\n".join(cur + [line])) + 4 > MAX_SECTION:
            chunks.append("\n".join(cur + (["```"] if fenced else [])))
            cur = ["```"] if fenced else []
        cur.append(line)
        if line.startswith("```"):
            fenced = not fenced
    chunks.append("\n".join(cur))
    return [{"type": "section", "text": {"type": "mrkdwn", "text": c}} for c in chunks if c.strip("`\n")]


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


class Slack:
    def __init__(self, token, dry_run):
        self.token = token
        self.dry_run = dry_run

    def call(self, method, **params):
        if self.dry_run and method != "users.lookupByEmail":
            print(f"--- {method} {params.get('channel', '')} thread={params.get('thread_ts', '')}")
            for b in json.loads(params.get("blocks", "[]")):
                print(
                    b.get("text", {}).get("text")
                    or " | ".join(f["text"] for f in b.get("fields", []))
                    or " ".join(e["text"] for e in b.get("elements", []))
                    or "-" * 40
                )
            if "blocks" not in params:
                print(params.get("text", ""))
            print()
            return {"ok": True, "ts": "dry-run", "channel": {"id": "dry-run"}}
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(
            SLACK_API + method, data=data, headers={"Authorization": f"Bearer {self.token}"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.load(resp)
        if not body.get("ok") and body.get("error") != "users_not_found":
            raise RuntimeError(
                f"Slack {method} failed: {body.get('error')} {body.get('response_metadata', '')}"
            )
        return body

    def channel(self, target):
        """A channel name or ID as is; a member ID (U...) becomes their DM channel."""
        if re.fullmatch(r"[UW][A-Z0-9]+", target):
            return self.call("conversations.open", users=target)["channel"]["id"]
        return target

    def post(self, channel, text, blocks=None, thread_ts=None):
        params = {"channel": channel, "text": text, "unfurl_links": "false", "unfurl_media": "false"}
        if blocks:
            params["blocks"] = json.dumps(blocks)
        if thread_ts:
            params["thread_ts"] = thread_ts
        return self.call("chat.postMessage", **params)["ts"]


def slack_id(user, cfg, slack, cache):
    """Slack member ID for a Slurm user, from the map, else by email; cached for a day."""
    if user in cache and cache[user]["checked"] > time.time() - 86400:
        return cache[user]["id"]
    target = cfg["user_map"].get(user) or (f"{user}@{cfg['email_domain']}" if cfg["email_domain"] else "")
    member = ""
    if re.fullmatch(r"[UW][A-Z0-9]+", target):
        member = target
    elif "@" in target and slack.token:
        member = slack.call("users.lookupByEmail", email=target).get("user", {}).get("id", "")
    cache[user] = {"id": member, "checked": time.time()}
    return member


# --- Main -----------------------------------------------------------------------

CONFIG_KEYS = {
    "slack_token": str, "channel": str, "prometheus_url": str, "idle_hours": (int, float),
    "idle_util_threshold": (int, float), "notify": list, "notify_repeat_hours": (int, float),
    "dm_text": str, "cancel_after_hours": (int, float), "user_map": dict, "email_domain": str,
    "state_file": str,
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
        if not isinstance(cfg.get(key), kind) or isinstance(cfg.get(key), bool):
            raise ConfigError(f"{path}: {key} is missing or not a {getattr(kind, '__name__', 'number')}")
    if set(cfg["notify"]) - NOTIFY:
        raise ConfigError(f"{path}: notify takes only {', '.join(sorted(NOTIFY))}")
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
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.check_config:
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

    def who(user, mention):
        member = slack_id(user, cfg, slack, cache) if mention else ""
        return f"<@{member}> ({user})" if member else user

    if preview:  # a preview notifies, cancels and records nothing
        post_report(report, cfg, who, slack, args.to)
        return
    # From here the state is saved whatever fails: when each job turned idle and whom
    # this run notified, so a failure is not followed by notifying everyone again.
    try:
        act(report, cfg, who, slack, cache, now)
    finally:
        write_atomic(cfg["state_file"], json.dumps(state), mode=0o600)


def post_report(report, cfg, who, slack, to):
    """The summary card and its thread; returns (channel, ts) of the card."""
    (text, summary), thread = render(report, cfg, who)
    channel = slack.channel(to) if to else cfg["channel"]
    ts = slack.post(channel, text, summary)
    for t, blocks in thread:
        slack.post(channel, t, blocks, thread_ts=ts)
    return channel, ts


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
        for j in report["idle"]:
            # Only once the owner was told in an earlier run: a lost state file or a
            # failed notification never turns into a cancel without warning.
            told = j["state"]["notified"]
            if told is None or told >= now or now - j["state"]["since"] < cfg["cancel_after_hours"] * 3600:
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
