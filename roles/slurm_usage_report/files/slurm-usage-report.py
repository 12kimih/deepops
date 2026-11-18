#!/usr/bin/env python3
"""Post a Slurm GPU usage report to Slack and nudge owners of idle GPU jobs.

Reads running jobs from scontrol, per-GPU utilisation from Prometheus (dcgm-exporter),
and node state from sinfo. Posts a one-line summary with the detail in its thread,
DMs the owner of each job whose GPUs all stayed idle for the whole window, and can
optionally cancel jobs idle for longer. Standard library only.
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

SLACK_API = "https://slack.com/api/"
CHUNK = 3500  # characters per thread message; keeps each one readable


def run(*cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def expand_ranges(spec):
    """'0-2,5' -> [0, 1, 2, 5]"""
    out = []
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        out.extend(range(int(lo), int(hi or lo) + 1))
    return out


def expand_hostlist(spec):
    """'gpu[1-3,5],master' -> ['gpu1', 'gpu2', 'gpu3', 'gpu5', 'master']"""
    hosts = []
    for prefix, body, plain in re.findall(r"([^,\[]+)\[([^\]]+)\]|([^,]+)", spec):
        if plain:
            hosts.append(plain)
            continue
        for part in body.split(","):
            lo, _, hi = part.partition("-")
            width = len(lo)
            hosts.extend(f"{prefix}{n:0{width}d}" for n in range(int(lo), int(hi or lo) + 1))
    return hosts


def seconds(runtime):
    """Slurm '[D-]HH:MM:SS' or 'MM:SS' -> seconds."""
    days, _, clock = runtime.rpartition("-")
    parts = [int(p) for p in clock.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return int(days or 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def duration(secs):
    days, rest = divmod(int(secs), 86400)
    clock = f"{rest // 3600}:{rest % 3600 // 60:02d}:{rest % 60:02d}"
    return f"{days}-{clock}" if days else clock


def field(line, key):
    m = re.search(rf"(?:^| ){key}=(\S*)", line)
    return m.group(1) if m else ""


def running_jobs():
    """Running jobs with the GPUs they hold: [{id, user, partition, qos, runtime, gpus}]"""
    jobs = []
    for line in run("scontrol", "show", "job", "-d", "-o").splitlines():
        if field(line, "JobState") != "RUNNING":
            continue
        gpus = []
        for nodes, gres in re.findall(r" Nodes=(\S+) CPU_IDs=\S+ Mem=\S+ GRES=(\S*)", line):
            idx = [i for spec in re.findall(r"gpu[^,(]*\(IDX:([^)]+)\)", gres) for i in expand_ranges(spec)]
            gpus.extend((host, i) for host in expand_hostlist(nodes) for i in idx)
        jobs.append({
            "id": field(line, "JobId"),
            "user": field(line, "UserId").split("(")[0],
            "partition": field(line, "Partition"),
            "qos": field(line, "QOS"),
            "runtime": seconds(field(line, "RunTime")),
            "gpus": gpus,
        })
    return jobs


def node_states():
    """{node: (state, gpu_count)} and the drain/down list with reasons."""
    nodes = {}
    for line in run("sinfo", "-h", "-N", "-o", "%n|%T|%G").splitlines():
        name, state, gres = line.split("|", 2)
        count = sum(int(n) for n in re.findall(r"gpu(?::[^:,(]+)?:(\d+)", gres))
        nodes[name] = (state, count)
    reasons = []
    for line in run("sinfo", "-h", "-R", "-o", "%n|%T|%H|%u|%E").splitlines():
        reasons.append(line.split("|", 4))
    return nodes, sorted(reasons)


def pending_summary():
    """Per user: jobs, GPUs requested, reasons, longest wait (s)."""
    users = defaultdict(lambda: {"jobs": 0, "gpus": 0, "reasons": Counter(), "wait": 0})
    now = time.time()
    for line in run("squeue", "-h", "-t", "PD", "-o", "%u|%D|%b|%V|%r").splitlines():
        user, nnodes, tres, submit, reason = line.split("|", 4)
        per_node = sum(int(n) for n in re.findall(r"gpu(?::[^:,]+)?:(\d+)", tres))
        entry = users[user]
        entry["jobs"] += 1
        entry["gpus"] += per_node * int(nnodes or 1)
        entry["reasons"][reason] += 1
        try:
            entry["wait"] = max(entry["wait"], now - datetime.fromisoformat(submit).timestamp())
        except ValueError:
            pass
    return users


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


class Slack:
    def __init__(self, token, dry_run):
        self.token = token
        self.dry_run = dry_run

    def call(self, method, **params):
        if self.dry_run and method not in ("users.lookupByEmail",):
            print(f"--- {method} {params.get('channel', '')} thread={params.get('thread_ts', '')}\n{params.get('text', '')}\n")
            return {"ok": True, "ts": "dry-run", "channel": {"id": "dry-run"}}
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(SLACK_API + method, data=data,
                                     headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.load(resp)
        if not body.get("ok") and body.get("error") != "users_not_found":
            raise RuntimeError(f"Slack {method} failed: {body.get('error')}")
        return body

    def post(self, channel, text, thread_ts=None):
        params = {"channel": channel, "text": text, "unfurl_links": "false"}
        if thread_ts:
            params["thread_ts"] = thread_ts
        return self.call("chat.postMessage", **params)["ts"]

    def dm(self, user_id, text):
        channel = self.call("conversations.open", users=user_id)["channel"]["id"]
        self.post(channel, text)


def slack_id(user, cfg, slack, cache):
    """Slack member ID for a Slurm user, from the map, else by email; cached."""
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


def chunks(lines):
    out, cur = [], ""
    for line in lines:
        if cur and len(cur) + len(line) + 1 > CHUNK:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        out.append(cur)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="/etc/slurm-usage-report/config.json")
    ap.add_argument("--dry-run", action="store_true", help="print instead of posting; never DM or cancel")
    args = ap.parse_args()
    with open(args.config) as f:
        cfg = json.load(f)
    try:
        with open(cfg["state_file"]) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    idle_state = state.setdefault("idle", {})
    user_cache = state.setdefault("users", {})

    window = int(cfg["idle_hours"] * 3600)
    threshold = cfg["idle_util_threshold"]
    prom = Prometheus(cfg["prometheus_url"])
    slack = Slack(cfg["slack_token"], args.dry_run)
    now = time.time()

    jobs = running_jobs()
    util_max = prom.per_gpu(f"max_over_time(DCGM_FI_DEV_GPU_UTIL[{window}s])")
    util_now = prom.per_gpu("avg_over_time(DCGM_FI_DEV_GPU_UTIL[10m])")
    mem_gib = prom.per_gpu("DCGM_FI_DEV_FB_USED / 1024")
    nodes, reasons = node_states()
    pending = pending_summary()

    total_gpus = sum(c for _, c in nodes.values())
    alloc_gpus = sum(len(j["gpus"]) for j in jobs)
    busy = [util_now[g] for j in jobs for g in j["gpus"] if g in util_now]
    avg_util = sum(busy) / len(busy) if busy else 0

    # A job is idle when it has run for the whole window and every GPU stayed at or
    # under the threshold throughout. GPUs without data never count as idle.
    idle = []
    for j in jobs:
        seen = [util_max[g] for g in j["gpus"] if g in util_max]
        j["idle_gpus"] = sum(1 for v in seen if v <= threshold)
        j["idle"] = (j["runtime"] >= window and j["gpus"] and len(seen) == len(j["gpus"])
                     and j["idle_gpus"] == len(j["gpus"]))
        if j["idle"]:
            idle.append(j)

    seen_ids = {j["id"] for j in idle}
    for jid in list(idle_state):
        if jid not in seen_ids:
            del idle_state[jid]
    # Owners are notified when a job first turns idle, then every notify_repeat_hours,
    # not on every report.
    repeat = cfg["notify_repeat_hours"] * 3600
    for j in idle:
        s = idle_state.setdefault(j["id"], {"since": now - window, "notified": 0})
        j["state"] = s
        j["notify"] = bool(cfg["notify"]) and now - s["notified"] >= repeat

    def who(user, mention):
        member = slack_id(user, cfg, slack, user_cache) if mention else ""
        return f"<@{member}> ({user})" if member else user

    summary = (f":bar_chart: *[GPU usage]* {datetime.fromtimestamp(now):%Y-%m-%d %H:%M} | "
               f"{alloc_gpus}/{total_gpus} GPUs allocated ({100 * alloc_gpus / max(total_gpus, 1):.0f}%), "
               f"avg util {avg_util:.0f}% | idle jobs {len(idle)} | drain/down nodes {len(reasons)} | "
               f"pending {sum(p['jobs'] for p in pending.values())} jobs")

    lines = []
    if idle:
        mention = "mention" in cfg["notify"]
        lines.append(f"*Idle for {cfg['idle_hours']:g}h+ (GPU util <= {threshold:g}%)*")
        for j in sorted(idle, key=lambda j: -len(j["gpus"])):
            tag = who(j["user"], mention and j["notify"])
            lines.append(f"- {tag} job {j['id']} / {len(j['gpus'])} GPU / {duration(j['runtime'])} / {j['partition']}")
        lines.append("")
    if reasons:
        lines.append("*Drain/down nodes*")
        lines.extend(f"- {n} / {st} / {ts} / {u} / {why}" for n, st, ts, u, why in reasons)
        lines.append("")
    if pending:
        lines.append("*Pending*")
        for user, p in sorted(pending.items(), key=lambda kv: -kv[1]["gpus"]):
            why = ", ".join(f"{r} {c}" for r, c in p["reasons"].most_common(2))
            lines.append(f"- {user}: {p['jobs']} jobs / {p['gpus']} GPU / longest {duration(p['wait'])} / {why}")
        lines.append("")

    by_user = defaultdict(list)
    for j in jobs:
        if j["gpus"]:
            by_user[j["user"]].append(j)
    for user, ujobs in sorted(by_user.items(), key=lambda kv: -sum(len(j["gpus"]) for j in kv[1])):
        n = sum(len(j["gpus"]) for j in ujobs)
        lines.append(f"*{user}* {n} GPU ({100 * n / max(total_gpus, 1):.1f}%) / {len(ujobs)} jobs")
        for j in sorted(ujobs, key=lambda j: -j["runtime"]):
            u = [util_now[g] for g in j["gpus"] if g in util_now]
            m = sum(mem_gib.get(g, 0) for g in j["gpus"])
            hosts = ",".join(sorted({h for h, _ in j["gpus"]}))
            note = "  <- idle" if j["idle"] else (f"  <- {j['idle_gpus']}/{len(j['gpus'])} GPU idle" if j["idle_gpus"] else "")
            util = f"{sum(u) / len(u):.0f}%" if u else "n/a"
            lines.append(f"- {j['id']} / {len(j['gpus'])} GPU / {hosts} / {duration(j['runtime'])} / "
                         f"{j['qos']} / util {util} / mem {m:.1f}G{note}")

    ts = slack.post(cfg["channel"], summary)
    for text in chunks(lines):
        slack.post(cfg["channel"], text, thread_ts=ts)

    if not args.dry_run:
        for j in idle:
            if not j["notify"]:
                continue
            if "dm" in cfg["notify"]:
                member = slack_id(j["user"], cfg, slack, user_cache)
                if member:
                    slack.dm(member, cfg["dm_text"].format(job=j["id"], gpus=len(j["gpus"]),
                                                           hours=f"{cfg['idle_hours']:g}", runtime=duration(j["runtime"])))
            j["state"]["notified"] = now

    if cfg["cancel_after_hours"] > 0 and not args.dry_run:
        cancelled = [j for j in idle if now - j["state"]["since"] >= cfg["cancel_after_hours"] * 3600]
        for j in cancelled:
            subprocess.run(["scancel", j["id"]], check=False)
        if cancelled:
            slack.post(cfg["channel"], "\n".join(
                f":no_entry: {who(j['user'], bool(cfg['notify']))} job {j['id']} was cancelled after "
                f"{cfg['cancel_after_hours']:g}h with idle GPUs" for j in cancelled), thread_ts=ts)

    if not args.dry_run:
        with open(cfg["state_file"], "w") as f:
            json.dump(state, f)


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, RuntimeError, OSError) as e:
        print(f"slurm-usage-report: {e}", file=sys.stderr)
        sys.exit(1)
