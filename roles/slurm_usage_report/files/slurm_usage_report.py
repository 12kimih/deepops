"""Post a Slurm GPU usage report to Slack and notify owners of idle GPU jobs.

Reads jobs and node state from Slurm's JSON output, per-GPU utilisation from
Prometheus (dcgm-exporter). Posts a summary card with the detail in its thread, and
mentions (or DMs) the owner of each job whose GPUs all stayed idle for the whole
window. Can optionally cancel jobs idle for longer. Standard library only.
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime

from clustertools.slurm import (GPU_CONFIGURED, GPU_REQUESTED, job_gpus, job_label, node_state,
                                num, scontrol)

SLACK_API = "https://slack.com/api/"
MAX_BLOCKS = 45      # Slack allows 50 blocks per message
MAX_SECTION = 2900   # and 3000 characters per section text
BAR_WIDTH = 10


# --- Slurm ----------------------------------------------------------------------
# Reading Slurm is clustertools.slurm's job (the cluster_tools_lib role): one reader for
# every tool. What is left here is what this report makes of it.

def duration(secs):
    days, rest = divmod(int(secs), 86400)
    clock = f"{rest // 3600}:{rest % 3600 // 60:02d}:{rest % 60:02d}"
    return f"{days}-{clock}" if days else clock


def parse_jobs(data, now):
    """Running jobs with the GPUs each holds, and pending jobs per user, from
    `scontrol -d --json show job`."""
    running = []
    pending = defaultdict(lambda: {"jobs": 0, "gpus": 0, "reasons": Counter(), "wait": 0})
    for job in data["jobs"]:
        state = job.get("job_state") or []
        if "RUNNING" in state:
            gpus, types = job_gpus(job)
            running.append({
                "id": str(job["job_id"]),
                "label": job_label(job),
                "user": job["user_name"],
                "partition": job.get("partition", ""),
                "qos": job.get("qos", ""),
                "runtime": max(0, now - (num(job.get("start_time")) or now)),
                "gpus": gpus,
                "gpu_type": ",".join(sorted(types)),
            })
        elif "PENDING" in state:
            entry = pending[job["user_name"]]
            entry["jobs"] += 1
            entry["gpus"] += sum(int(n) for n in GPU_REQUESTED.findall(job.get("tres_req_str") or ""))
            entry["reasons"][job.get("state_reason") or "None"] += 1
            submit = num(job.get("submit_time"))
            if submit:
                entry["wait"] = max(entry["wait"], now - submit)
    return running, dict(pending)


def parse_nodes(data):
    """({node: gpu count}, [(node, state, since, user, reason)]) from `scontrol --json show nodes`."""
    gpus, unavailable = {}, []
    for node in data["nodes"]:
        gpus[node["name"]] = sum(int(n) for n in GPU_CONFIGURED.findall(node.get("gres") or ""))
        state = node_state(node.get("state") or [])
        if state:
            changed = num(node.get("reason_changed_at"))
            since = datetime.fromtimestamp(changed).isoformat(timespec="seconds") if changed else ""
            unavailable.append((node["name"], state, since, node.get("reason_set_by_user") or "",
                                node.get("reason") or ""))
    return gpus, sorted(unavailable)


def collect_slurm(now):
    jobs, pending = parse_jobs(scontrol("-d", "show", "job"), now)
    nodes, reasons = parse_nodes(scontrol("show", "nodes"))
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
        """{(host, gpu index): value}. host is the scrape target without its port."""
        out = {}
        for r in self.query(expr):
            m = r["metric"]
            host = m.get("host") or m.get("instance", "").rsplit(":", 1)[0]
            out[(host, int(m.get("gpu", -1)))] = float(r["value"][1])
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
        j["mem_gib"] = sum(gpu["mem_gib"].get(g, 0) for g in j["gpus"])
        j["idle_gpus"] = sum(1 for v in peaks if v <= threshold)
        j["idle"] = (bool(j["gpus"]) and j["runtime"] >= window and len(peaks) == len(j["gpus"])
                     and j["idle_gpus"] == len(j["gpus"]))
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


def table(header, rows):
    """A fixed-width table in a code block; Slack renders it monospaced."""
    widths = [max(len(str(c)) for c in col) for col in zip(header, *rows)]

    def fmt(r):
        return "  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip()

    return "```\n" + "\n".join([fmt(header)] + [fmt(r) for r in rows]) + "\n```"


def section(text):
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def render(report, cfg, who):
    """Summary (text, blocks) and the thread messages [(text, blocks)]."""
    jobs, idle, total = report["jobs"], report["idle"], max(report["total_gpus"], 1)
    alloc = sum(len(j["gpus"]) for j in jobs)
    utils = [j["util"] for j in jobs if j["util"] is not None for _ in j["gpus"]]
    avg = sum(utils) / len(utils) if utils else 0
    pending = report["pending"]
    pend_jobs = sum(p["jobs"] for p in pending.values())
    pend_gpus = sum(p["gpus"] for p in pending.values())
    stamp = datetime.fromtimestamp(report["now"]).strftime("%Y-%m-%d %H:%M")

    text = (f"GPU usage {stamp}: {alloc}/{total} GPUs allocated ({100 * alloc / total:.0f}%), "
            f"avg util {avg:.0f}%, {len(idle)} idle jobs")
    status = ":large_green_circle:" if not idle and not report["reasons"] else ":large_yellow_circle:"
    fields = [
        f"*Allocated*\n{alloc} / {total} GPU ({100 * alloc / total:.0f}%)",
        f"*Average utilisation*\n{avg:.0f}%",
        f"*Idle jobs*\n{len(idle)}" + (" :zzz:" if idle else ""),
        f"*Pending*\n{pend_jobs} jobs, {pend_gpus} GPU",
        f"*Drain / down*\n{len(report['reasons'])} nodes" + (" :warning:" if report["reasons"] else ""),
    ]
    footer = "Details in the thread"
    if cfg.get("dashboard_url"):
        footer += f"  |  <{cfg['dashboard_url']}|Grafana>"
    summary = [
        {"type": "header", "text": {"type": "plain_text", "text": f"\U0001F4CA GPU usage  {stamp}"}},
        {"type": "section", "fields": [{"type": "mrkdwn", "text": f} for f in fields]},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"{status}  {footer}"}]},
    ]

    blocks = []
    if idle:
        rule = f"every GPU at or under {cfg['idle_util_threshold']:g}% for {cfg['idle_hours']:g}h+"
        lines = [f":zzz: *Idle GPU jobs*  _{rule}_"]
        for j in sorted(idle, key=lambda j: -len(j["gpus"])):
            hosts = ",".join(sorted({h for h, _ in j["gpus"]}))
            lines.append(f"• {who(j['user'], 'mention' in cfg['notify'] and j['notify'])}  "
                         f"`{j['label']}`  {len(j['gpus'])}x {j['gpu_type'] or 'GPU'} on {hosts}  "
                         f"running {duration(j['runtime'])}")
        lines.append("_Cancel with `scancel <job>` if you no longer need it._")
        blocks.append(section("\n".join(lines)))
    if report["reasons"]:
        rows = [(n, st, since.replace("T", " ")[:16], u, why) for n, st, since, u, why in report["reasons"]]
        blocks.append(section(":warning: *Drain / down nodes*\n"
                              + table(("NODE", "STATE", "SINCE", "BY", "REASON"), rows)))
    if pending:
        rows = [(u, p["jobs"], p["gpus"], duration(p["wait"]),
                 ", ".join(f"{r.split(',')[0][:32]} ({c})" for r, c in p["reasons"].most_common(2)))
                for u, p in sorted(pending.items(), key=lambda kv: -kv[1]["gpus"])]
        blocks.append(section(":hourglass_flowing_sand: *Pending*\n"
                              + table(("USER", "JOBS", "GPU", "LONGEST", "REASON"), rows)))

    by_user = defaultdict(list)
    for j in jobs:
        if j["gpus"]:
            by_user[j["user"]].append(j)
    if by_user:
        blocks.append({"type": "divider"})
        blocks.append(section(":busts_in_silhouette: *Running GPU jobs by user*"))
    for user, ujobs in sorted(by_user.items(), key=lambda kv: -sum(len(j["gpus"]) for j in kv[1])):
        n = sum(len(j["gpus"]) for j in ujobs)
        rows = []
        for j in sorted(ujobs, key=lambda j: -j["runtime"]):
            hosts = ",".join(sorted({h for h, _ in j["gpus"]}))
            note = "idle" if j["idle"] else (f"{j['idle_gpus']}/{len(j['gpus'])} idle" if j["idle_gpus"] else "")
            rows.append((j["label"], f"{len(j['gpus'])}x {j['gpu_type'] or 'gpu'}", hosts,
                         duration(j["runtime"]), j["qos"], bar(j["util"]), f"{j['mem_gib']:.0f}G", note))
        head = f"*{user}*  {n} GPU ({100 * n / total:.1f}%)  |  {len(ujobs)} job{'s' if len(ujobs) > 1 else ''}"
        body = table(("JOB", "GPU", "NODE", "TIME", "QOS", "UTIL", "MEM", ""), rows)
        if len(head) + len(body) > MAX_SECTION:  # a very long table is cut, not rejected by Slack
            body = body[:MAX_SECTION - len(head) - 8] + "\n...\n```"
        blocks.append(section(f"{head}\n{body}"))

    thread = [("GPU usage details", blocks[i:i + MAX_BLOCKS]) for i in range(0, len(blocks), MAX_BLOCKS)]
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
                print(b.get("text", {}).get("text")
                      or " | ".join(f["text"] for f in b.get("fields", []))
                      or " ".join(e["text"] for e in b.get("elements", []))
                      or "-" * 40)
            if "blocks" not in params:
                print(params.get("text", ""))
            print()
            return {"ok": True, "ts": "dry-run", "channel": {"id": "dry-run"}}
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(SLACK_API + method, data=data,
                                     headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.load(resp)
        if not body.get("ok") and body.get("error") != "users_not_found":
            raise RuntimeError(f"Slack {method} failed: {body.get('error')} {body.get('response_metadata', '')}")
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

def report_once():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="/etc/slurm-usage-report/config.json")
    ap.add_argument("--dry-run", action="store_true", help="print instead of posting")
    ap.add_argument("--to", metavar="CHANNEL_OR_MEMBER",
                    help="post a preview there instead; notifies, cancels and records nothing")
    args = ap.parse_args()
    with open(args.config) as f:
        cfg = json.load(f)
    preview = args.dry_run or bool(args.to)
    try:
        with open(cfg["state_file"]) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}

    now = time.time()
    window = int(cfg["idle_hours"] * 3600)
    slack = Slack(cfg["slack_token"], args.dry_run)
    report = collect_slurm(now)
    gpu = collect_gpus(Prometheus(cfg["prometheus_url"]), window)
    report.update(now=now, total_gpus=sum(report["nodes"].values()),
                  idle=analyze(report["jobs"], gpu, window, cfg["idle_util_threshold"]))
    track_idle(report["idle"], state.setdefault("idle", {}), now, window,
               cfg["notify_repeat_hours"] * 3600, [] if preview else cfg["notify"])
    cache = state.setdefault("users", {})

    def who(user, mention):
        member = slack_id(user, cfg, slack, cache) if mention else ""
        return f"<@{member}> ({user})" if member else user

    (text, summary), thread = render(report, cfg, who)
    channel = slack.channel(args.to) if args.to else cfg["channel"]
    ts = slack.post(channel, text, summary)
    for t, blocks in thread:
        slack.post(channel, t, blocks, thread_ts=ts)
    if preview:
        return

    for j in report["idle"]:
        if not j["notify"]:
            continue
        if "dm" in cfg["notify"]:
            member = slack_id(j["user"], cfg, slack, cache)
            if member:
                slack.post(slack.channel(member), cfg["dm_text"].format(
                    job=j["label"], gpus=len(j["gpus"]), hours=f"{cfg['idle_hours']:g}",
                    runtime=duration(j["runtime"])))
        j["state"]["notified"] = now

    if cfg["cancel_after_hours"] > 0:
        cancelled = [j for j in report["idle"] if now - j["state"]["since"] >= cfg["cancel_after_hours"] * 3600]
        for j in cancelled:
            subprocess.run(["scancel", j["id"]], check=False)
        if cancelled:
            slack.post(channel, "Idle jobs cancelled", [section("\n".join(
                f":no_entry: {who(j['user'], bool(cfg['notify']))}  `{j['label']}` was cancelled after "
                f"{cfg['cancel_after_hours']:g}h with idle GPUs" for j in cancelled))], thread_ts=ts)

    with open(cfg["state_file"], "w") as f:
        json.dump(state, f)


def main():
    try:
        report_once()
    except (subprocess.CalledProcessError, RuntimeError, OSError, ValueError) as e:
        print(f"slurm-usage-report: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
