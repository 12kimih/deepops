"""sq: the Slurm queue, who holds what, why jobs wait, and what each partition pool has left.

Every section comes from one snapshot (scontrol --json show nodes, partitions and jobs:
three controller calls), so the tables always agree with each other. The filters are
squeue's own (-u -p -w -t -j -A -q -n --me) and apply to every job-based section.
build() turns the snapshot into a model and the show_* functions draw it; --json prints
the model itself, so a script reads the figures a person sees.
"""

import json
import os
import pwd
import re
import shutil
import sys
import time
from fnmatch import fnmatchcase

from clustertools import render, slurm, vocab
from clustertools.cli import Command, Opt, run_main
from clustertools.render import Grid, width

MIN_INTERVAL = 2  # slurmctld's per-user rate limit enforces; this only guards
MAX_BACKOFF = 60  # the longest wait while Slurm fails or does not answer
NAME_MAX = 30  # cap for pool and node names
STATE_MAX = 22  # cap for STATE, which --detail can make long
BAR = 10  # utilisation bar width (shrinks to fit)
BAR_MIN = 4  # below this the bars are dropped
FAMILY = re.compile(r"^(.+)-[0-9]+$")  # partitions differing only by this index form one pool


# --- Command line -------------------------------------------------------------------


def _interval(text):
    seconds = int(text)
    if seconds < MIN_INTERVAL:
        raise ValueError
    return seconds


_interval.__name__ = f"whole number of seconds, at least {MIN_INTERVAL}"


def _keys():
    """The state and reason keys under --help, from the vocabulary that classifies."""
    lines = ["Node states are sinfo's base words; a flag that closes a node to new work wins:"]
    lines += [f"  {c:<6} {vocab.NODE_HELP[c]}" for c in vocab.NODE_CLASSES]
    lines += ["  --detail prints the full state instead, e.g. mixed+drain, idle+powered_down.", ""]
    lines.append("Pending jobs show Slurm's reason code, in families as job_reason_codes groups them:")
    for c in vocab.REASON_CLASSES:
        lines += [
            f"  {vocab.REASON_TITLE[c]:<21} {vocab.REASON_CODES[c]}",
            f"  {'':<21} {vocab.REASON_HELP[c]}",
        ]
    return "\n".join(lines)


_SQUEUE = "use squeue itself for that; sq draws its own tables"
COMMAND = Command(
    "sq",
    "the Slurm queue, who holds what, and what each partition pool has left",
    [
        Opt("-m", "--me", brief=True, section="Filters", help="only your jobs"),
        Opt("-u", "--user", metavar="USER", complete="users", brief=True, section="Filters",
            help="only these users (comma list; * and ? match)"),
        Opt("-p", "--partition", metavar="PART", complete="partitions", brief=True, section="Filters",
            help="only these partitions"),
        Opt("-w", "--nodelist", metavar="NODES", complete="nodes", brief=True, section="Filters",
            help="only these nodes (a hostlist like gpu[1-3,8], or globs)"),
        Opt("-t", "--states", "--state", metavar="STATES", section="Filters",
            help="only these job states (R,PD or running,pending; all includes finished)"),
        Opt("-j", "--jobs", metavar="IDS", section="Filters", help="only these job IDs"),
        Opt("-A", "--account", metavar="ACCOUNTS", section="Filters", help="only these accounts"),
        Opt("-q", "--qos", metavar="QOS", section="Filters", help="only these QoS"),
        Opt("-n", "--name", metavar="NAMES", section="Filters", help="only these job names"),
        Opt("--queue", metavar="WHICH", choices=("mine", "all", "none"), default="mine", section="Sections",
            help="the job list: mine (default; a job filter shows what it selects), all, or none"),
        Opt("-Q", dest="queue", action="store_const", const="all", brief=True, section="Sections",
            help="everyone's jobs in the job list (--queue all)"),
        Opt("--no-queue", dest="queue", action="store_const", const="none", section="Sections",
            help="no job list (--queue none)"),
        Opt("-U", "--users", negate=True, default=True, section="Sections",
            help="who holds what, and each user's share of the GPUs"),
        Opt("-W", "--why", negate=True, default=False, brief=True, section="Sections",
            help="why jobs wait: reason families, then Slurm's codes"),
        Opt("--resources", negate=True, default=True, section="Sections", help="the CPU / memory / GPU table"),
        Opt("-d", "--detail", negate=True, default=False, brief=True, section="Sections",
            help="full node states and reasons, and who is behind each pending reason"),
        Opt("--group", negate=True, default=True, section="Sections",
            help="pending jobs above running ones (--no-group: job ID order)"),
        Opt("--split", negate=True, default=False, section="Sections",
            help="break each pool into the partitions it is split across"),
        Opt("-T", "--totals", "--show-partitions", dest="view", action="store_const", const="partitions",
            default="grouped", brief=True, section="Layout", help="pool totals only"),
        Opt("-N", "--nodes", "--show-nodes", dest="view", action="store_const", const="nodes", brief=True,
            section="Layout", help="a flat node list, as in sinfo -N"),
        Opt("--gres", metavar="NAME", section="Layout",
            help="the generic resource to count (default: the one most nodes have)"),
        Opt("-i", "--interval", "--iterate", metavar="SEC", type=_interval, brief=True, section="Layout",
            help=f"redraw every SEC seconds (at least {MIN_INTERVAL}); while Slurm fails or does not "
            f"answer, the wait doubles, up to {MAX_BACKOFF}"),
        Opt("--json", section="Output", help="the model as JSON, for scripts (ignores $SQ_OPTIONS)"),
    ],
    env="SQ_OPTIONS",
    machine="--json",
    theme_env="SQUEUE_THEME",
    notes=[
        "Defaults: $SQ_OPTIONS is read as if typed before the arguments (export SQ_OPTIONS=\"--no-users -W\"),\n"
        "so the command line always has the last word. --json ignores it.",
        "A pool's use comes from its nodes, or from its own jobs when it shares nodes with another partition.",
        _keys,
    ],
    refuse={
        "-o": _SQUEUE, "--format": _SQUEUE, "-O": _SQUEUE, "--Format": _SQUEUE, "-l": _SQUEUE,
        "--long": _SQUEUE, "-S": _SQUEUE, "--sort": _SQUEUE, "--start": _SQUEUE, "-r": _SQUEUE,
        "--array": _SQUEUE, "--noheader": _SQUEUE, "--no-jobs": "the section switch is now --no-queue "
        "(-j/--jobs is squeue's job ID filter)",
    },
)  # fmt: skip


# --- Model --------------------------------------------------------------------------


def _globs(text):
    return [g for g in re.split(r"[,\s]+", text or "") if g]


def _any(values, patterns):
    return any(fnmatchcase(v, p) for v in values for p in patterns)


def _natural(name):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class Filters:
    """The squeue-style selection, applied to every job-based section."""

    def __init__(
        self, me="", user="", partition="", nodelist="", states="", jobs="", account="", qos="", name=""
    ):
        self.users = _globs(user) + ([me] if me else [])
        self.parts, self.accounts, self.qos, self.names = (
            _globs(partition), _globs(account), _globs(qos), _globs(name)
        )  # fmt: skip
        # -w takes Slurm hostlist syntax, as sinfo and squeue do; what is left is globs.
        self.nodes = slurm.hostnames(nodelist) if nodelist else []
        self.jobs = set(_globs(jobs))
        given = {s.upper() for s in _globs(states)}
        self.all_states = "ALL" in given
        self.states = {vocab.JOB_CODES.get(s, s) for s in given - {"ALL"}}

    def job(self, j):
        if self.states:
            if j["state"] not in self.states:
                return False
        elif not self.all_states and not j["active"]:
            return False
        checks = (
            (self.users, [j["user"]]), (self.parts, j["partitions"]), (self.nodes, j["hosts"]),
            (self.accounts, [j["account"]]), (self.qos, [j["qos"]]), (self.names, [j["name"]]),
        )  # fmt: skip
        if any(pats and not _any(vals, pats) for pats, vals in checks):
            return False
        return not self.jobs or bool(self.jobs & {str(j["id"]), j["label"], str(j["array"])})

    def narrows(self):
        """Was any job filter given? Then the job list shows what it selects."""
        return bool(self.users or self.parts or self.nodes or self.jobs or self.states or self.all_states
                    or self.accounts or self.qos or self.names)  # fmt: skip

    def node(self, node):
        if self.nodes and not _any([node["name"]], self.nodes):
            return False
        return not self.parts or _any(node["partitions"], self.parts)


def _read_nodes(data, gres):
    nodes = []
    for n in data["nodes"]:
        tres, used = slurm.tres_str(n["tres"]), slurm.tres_str(n["tres_used"])
        mem_t = (slurm.num(n["real_memory"]) or 0) - (slurm.num(n["specialized_memory"]) or 0)
        nodes.append({
            "name": n["name"], "state": vocab.node_state(n["state"]), "class": vocab.node_class(n["state"]),
            "partitions": list(n["partitions"] or []),
            "cpu_total": slurm.num(n["effective_cpus"]) or slurm.num(n["cpus"]) or 0,
            "cpu_used": slurm.num(n["alloc_cpus"]) or 0,
            "mem_total": mem_t, "mem_used": slurm.num(n["alloc_memory"]) or 0,
            "_tres": tres, "_used": used,
        })  # fmt: skip
    if not gres:  # the resource configured on the most nodes, ties by name
        counts = {}
        for n in nodes:
            for key in n["_tres"]:
                if key.startswith("gres/") and ":" not in key:
                    counts[key[5:]] = counts.get(key[5:], 0) + 1
        gres = min(counts, key=lambda k: (-counts[k], k)) if counts else "gpu"
    for n in nodes:
        n["gres_total"] = _int(n.pop("_tres").get(f"gres/{gres}"))
        n["gres_used"] = _int(n.pop("_used").get(f"gres/{gres}"))
    nodes.sort(key=lambda n: _natural(n["name"]))
    return nodes, gres


def _read_jobs(data, gres, now):
    jobs = []
    for j in data["jobs"]:
        holds = vocab.job_holds(j["job_state"])
        alloc = slurm.tres_str(j["tres_alloc_str"]) if holds else {}
        req = slurm.tres_str(j["tres_req_str"])
        start = slurm.num(j["start_time"])
        jobs.append({
            "id": j["job_id"], "label": slurm.job_label(j), "array": slurm.num(j["array_job_id"]) or "",
            "name": j["name"] or "", "user": j["user_name"], "account": j["account"] or "",
            "partition": j["partition"] or "", "partitions": [p for p in (j["partition"] or "").split(",") if p],
            "qos": j["qos"] or "", "state": vocab.job_code(j["job_state"]), "active": vocab.job_active(j["job_state"]),
            "holds": holds, "nodes": j["nodes"] or "", "hosts": slurm.hostnames(j["nodes"] or ""),
            "node_count": slurm.num(j["node_count"]) or 0, "cpus": slurm.num(j["cpus"]) or 0,
            "gres": (j["tres_per_node"] or "").replace("gres/", "").replace("gres:", ""),
            "elapsed": max(0, now - start) if holds and start else 0,
            "limit_minutes": slurm.num(j["time_limit"]),
            "limit_unlimited": isinstance(j["time_limit"], dict) and bool(j["time_limit"].get("infinite")),
            "priority": slurm.num(j["priority"]) or 0,
            "reason": j["state_reason"] or "None", "reason_detail": j["state_description"] or "",
            "tasks": slurm.array_tasks(j["array_task_string"]) if j["array_task_string"] else 1,
            "alloc": {"cpu": _int(alloc.get("cpu")), "mem": slurm.mem_mib(alloc.get("mem")),
                      "gres": _int(alloc.get(f"gres/{gres}"))},
            "req": {"nodes": _int(req.get("node")) or slurm.num(j["node_count"]) or 0,
                    "cpu": _int(req.get("cpu")) or slurm.num(j["cpus"]) or 0,
                    "gres": _int(req.get(f"gres/{gres}"))},
        })  # fmt: skip
    return jobs


def _family(partition):
    m = FAMILY.match(partition)
    return m.group(1) if m else partition


def build(nodes_data, parts_data, jobs_data, sel, me, gres=None, now=None, title=None):
    """The model of one snapshot: jobs, users, why jobs wait, and the resource pools.
    TITLE names the cluster row; Slurm's ClusterName otherwise."""
    now = int(time.time() if now is None else now)  # whole seconds, as Slurm keeps time
    nodes, key = _read_nodes(nodes_data, gres)
    by_name = {n["name"]: n for n in nodes}
    has_gres = any(n["gres_total"] for n in nodes)
    jobs = _read_jobs(jobs_data, key, now)

    # Partitions: slurm.conf order, then any seen only on a node.
    part_state = {p["name"]: p["partition"]["state"] for p in parts_data["partitions"]}
    members = {}
    for n in nodes:
        for p in n["partitions"]:
            members.setdefault(p, []).append(n["name"])
    partitions = [p for p in part_state if p in members] + [p for p in members if p not in part_state]

    # Job-based figures. Allocations count only while a job still holds them.
    held = [j for j in jobs if j["holds"]]
    chosen = [j for j in jobs if sel.job(j)]
    mine = {h for j in held if j["user"] == me for h in j["hosts"]}
    by_partition = {}
    for j in held:
        for p in j["partitions"][:1]:
            acc = by_partition.setdefault(p, {"cpu": 0, "mem": 0, "gres": 0})
            for k in acc:
                acc[k] += j["alloc"][k]

    users = {}
    for j in chosen:
        if not (j["holds"] or j["state"] == "PD"):
            continue  # a finished job lingers for MinJobAge with nothing to report
        u = users.setdefault(
            j["user"],
            {"user": j["user"], "running": 0, "pending": 0, "nodes": set(), "cpu": 0, "mem": 0, "gres": 0},
        )
        if j["holds"]:
            u["running"] += 1
            u["nodes"].update(j["hosts"])
            for k in ("cpu", "mem", "gres"):
                u[k] += j["alloc"][k]
        else:
            u["pending"] += j["tasks"]
    user_rows = sorted(
        users.values(), key=lambda u: (-u["gres"], -len(u["nodes"]), -u["cpu"], -u["pending"], u["user"])
    )
    user_nodes = len({h for u in user_rows for h in u["nodes"]})
    for u in user_rows:
        u["nodes"] = len(u["nodes"])

    why, details = {}, {}
    for j in chosen:
        if j["state"] != "PD":
            continue
        k = (vocab.reason_class(j["reason"]), j["reason"], j["partition"], j["user"])
        acc = why.setdefault(k, {"jobs": 0, "nodes": 0, "cpu": 0, "gres": 0})
        acc["jobs"] += j["tasks"]
        acc["nodes"] += j["req"]["nodes"] * j["tasks"]
        acc["cpu"] += j["req"]["cpu"] * j["tasks"]
        acc["gres"] += j["req"]["gres"] * j["tasks"]
        if j["reason_detail"]:
            details.setdefault(j["reason"], j["reason_detail"])

    # Pools: the cluster, each partition, each family of partitions.
    pools = {}

    def add(pool, n):
        a = pools.setdefault(pool, dict.fromkeys(
            ("nodes", "cpu_total", "cpu_used", "mem_total", "mem_used", "gres_total", "gres_used",
             "idle", "off", "free", "occupancy"), 0))  # fmt: skip
        a["nodes"] += 1
        for k in ("cpu_total", "cpu_used", "mem_total", "mem_used", "gres_total", "gres_used"):
            a[k] += n[k]
        a["idle"] += n["class"] == "idle"
        a["off"] += n["class"] not in vocab.OPEN_CLASSES
        a["free"] += _free(n, has_gres)
        # The binding resource sets how full a node is: every GPU taken is full.
        occ = 1000 * n["cpu_used"] // n["cpu_total"] if n["cpu_total"] else 0
        if n["gres_total"]:
            occ = max(occ, 1000 * n["gres_used"] // n["gres_total"])
        a["occupancy"] += min(occ, 1000)

    for n in nodes:
        add("c", n)
    exclusive = {"c": True}
    for p in partitions:
        exclusive[f"p/{p}"] = all(by_name[n]["partitions"] == [p] for n in members[p])
        for n in members[p]:
            add(f"p/{p}", by_name[n])
    families = {}
    for p in partitions:
        families.setdefault(_family(p), []).append(p)
    family_members = {}
    for fam, parts in families.items():
        names = sorted({n for p in parts for n in members[p]}, key=_natural)
        family_members[fam] = names
        exclusive[f"f/{fam}"] = all(_family(q) == fam for n in names for q in by_name[n]["partitions"])
        for n in names:
            add(f"f/{fam}", by_name[n])

    def used(pool, parts):
        """A pool's use: its nodes' allocation when it has them to itself, else its own
        jobs, since node figures cannot split a shared node."""
        a = pools[pool]
        if exclusive[pool]:
            return a["cpu_used"], a["mem_used"], a["gres_used"], "nodes"
        tot = [sum(by_partition.get(p, {}).get(k, 0) for p in parts) for k in ("cpu", "mem", "gres")]
        return (*tot, "jobs")

    def pool_row(kind, name, pool, parts, parent, state_of):
        a = pools[pool]
        cu, mu, gu, src = used(pool, parts)
        state = part_state.get(state_of) if state_of else None
        return {
            "kind": kind, "name": name, "parent": parent,
            "class": vocab.node_class(state) if state else "", "state": vocab.node_state(state) if state else "",
            "cpu_used": cu, "cpu_total": a["cpu_total"], "mem_used": mu, "mem_total": a["mem_total"],
            "gres_used": gu, "gres_total": a["gres_total"], "idle": a["idle"], "off": a["off"],
            "free": a["free"], "used_from": src, "nodes": a["nodes"], "occupancy": a["occupancy"],
        }  # fmt: skip

    def node_row(n, parent):
        return {
            "kind": "node", "name": n["name"], "parent": parent, "class": n["class"], "state": n["state"],
            "cpu_used": n["cpu_used"], "cpu_total": n["cpu_total"], "mem_used": n["mem_used"],
            "mem_total": n["mem_total"], "gres_used": n["gres_used"], "gres_total": n["gres_total"],
            "idle": None, "off": None, "free": _free(n, has_gres), "used_from": "nodes", "mine": n["name"] in mine,
            "partitions": n["partitions"],
        }  # fmt: skip

    cluster = slurm.cluster_name(nodes_data) or "cluster"
    pool_tree = {"cluster": pool_row("cluster", title or cluster, "c", partitions, "", None)}
    pool_tree["families"] = []
    for fam, parts in families.items():
        shown = [p for p in parts if not sel.parts or _any([p], sel.parts)]
        entry = {
            "name": fam, "parts": shown,
            "row": pool_row("family", fam, f"f/{fam}", parts, "", None) if len(parts) > 1 else None,
            "part_rows": [pool_row("part", p, f"p/{p}", [p], fam if len(parts) > 1 else "", p) for p in shown],
            "nodes": {p: [node_row(by_name[n], p) for n in sorted(members[p], key=_natural)] for p in shown},
            "family_nodes": [node_row(by_name[n], fam) for n in family_members[fam]],
        }  # fmt: skip
        pool_tree["families"].append(entry)

    return {
        "cluster": cluster, "gres": key, "has_gres": has_gres, "free_what": key if has_gres else "cpu",
        "gres_pinned": bool(gres), "jobs": [_public(j) for j in chosen],
        "users": user_rows, "user_nodes": user_nodes,
        "why": [{"family": k[0], "code": k[1], "partition": k[2], "user": k[3], **v} for k, v in why.items()],
        "why_details": details, "pools": pool_tree,
        "nodes": [node_row(n, "") for n in nodes], "node_filter": sel,
        "held_cpu": sum(j["alloc"]["cpu"] for j in held), "held_gres": sum(j["alloc"]["gres"] for j in held),
        "mine": me,
    }  # fmt: skip


def _free(n, has_gres):
    """What a node could still give a new job; nothing on one closed to work."""
    if n["class"] not in vocab.OPEN_CLASSES:
        return 0
    if has_gres:
        return max(0, n["gres_total"] - n["gres_used"])
    return max(0, n["cpu_total"] - n["cpu_used"])


def _public(j):
    return {k: v for k, v in j.items() if k not in ("hosts", "partitions", "alloc", "req", "active")}


def resource_rows(model, view, split, glyphs):
    """The resource table's rows for a view, as (prefix, row) pairs; None is a rule.
    GLYPHS draw the tree: end, tee, gap and pipe."""
    sel, pools, G = model["node_filter"], model["pools"], glyphs
    rows = [("", pools["cluster"])]

    def tree(cont, node_rows):
        shown = [r for r in node_rows if sel.node(r)]
        for i, r in enumerate(shown):
            rows.append((cont + (G["end"] if i == len(shown) - 1 else G["tee"]), r))

    if view == "nodes":
        rows.append(None)
        rows += [("", r) for r in model["nodes"] if sel.node(r)]
        return rows
    for fam in pools["families"]:
        if not fam["parts"]:
            continue
        if view != "partitions" and sel.nodes and not any(sel.node(r) for r in fam["family_nodes"]):
            continue
        rows.append(None)
        with_nodes = view != "partitions"
        if fam["row"] is None:  # a pool of one partition is that partition
            rows.append(("", fam["part_rows"][0]))
            if with_nodes:
                tree("", fam["nodes"][fam["parts"][0]])
            continue
        rows.append(("", fam["row"]))
        if not split:
            if with_nodes:
                tree("", fam["family_nodes"])
            continue
        for i, prow in enumerate(fam["part_rows"]):
            last = i == len(fam["part_rows"]) - 1
            rows.append((G["end"] if last else G["tee"], prow))
            if with_nodes:
                tree(G["gap"] if last else G["pipe"], fam["nodes"][prow["name"]])
    return rows


# --- Drawing ------------------------------------------------------------------------

NODE_ROLE = {"idle": "ok", "drain": "hot", "down": "crit"}
REASON_ROLE = {"wait": "warn", "limit": "hot", "over": "crit", "error": "crit", "nodes": "info", "start": "done",
               "held": "muted"}  # fmt: skip
JOB_STYLE = {  # squeue code -> (role, ASCII mark, mark)
    "R": ("ok", "*", "●"), "PD": ("warn", "o", "○"), "CG": ("info", "~", "◐"), "CD": ("done", "+", "✓"),
    **dict.fromkeys(("S", "ST", "PR"), ("hot", "=", "◼")),
    **dict.fromkeys(("F", "NF", "CA", "TO", "BF", "DL", "OOM"), ("crit", "x", "✕")),
}  # fmt: skip
STATE_LABEL = {"R": "running", "PD": "pending", "CG": "completing", "CD": "completed", "S": "suspended"}


def _clock(seconds):
    """squeue's duration: M:SS, H:MM:SS, then D-HH:MM:SS."""
    days, rest = divmod(int(seconds), 86400)
    h, rest = divmod(rest, 3600)
    m, s = divmod(rest, 60)
    if days:
        return f"{days}-{h:02d}:{m:02d}:{s:02d}"
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def show_jobs(t, model, group=True, detail=False, scope="mine"):
    """The job list. SCOPE mine shows your own jobs unless a job filter chose others;
    the sections below it summarise everyone's."""
    jobs = model["jobs"]
    me, dot = model["mine"], t.g["dot"]
    only_mine = scope == "mine" and not model["node_filter"].narrows()
    if only_mine:
        jobs = [j for j in jobs if j["user"] == me]
    if group:
        jobs = sorted(jobs, key=lambda j: (vocab.job_rank(j["state"]), -j["priority"], j["id"]))
    else:
        jobs = sorted(jobs, key=lambda j: j["id"])
    heads = ["JOBID", "ST", "NAME", "USER", "PARTITION", "QOS", "N", "NODE(REASON)", "CPU", "GRES", "TIME",
             "LIMIT", "PRIO"]  # fmt: skip
    align = ["r", "l", "l", "l", "l", "l", "r", "l", "r", "l", "r", "r", "r"]
    maxw = [20, 4, 20, 12, 12, 10, 3, 22, 5, 14, 10, 10, 11]  # a clipped job ID says nothing true
    minw = [5, 4, 10, 6, 4, 4, 1, 8, 3, 4, 5, 5, 4]
    want = [0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 2, 0]
    # Only free text narrows, and only where a reader can finish the word: a clipped
    # number says nothing true. 0 never narrows; lower narrows first.
    flex = [0, 0, 1, 3, 0, 0, 0, 2, 0, 0, 0, 0, 0]
    softw = [8, 4, 14, 7, 12, 10, 3, 16, 5, 14, 10, 10, 11]
    short = {7: "NODE"}

    rows = []
    for j in jobs:
        role, ascii_mark, mark = JOB_STYLE.get(j["state"], ("sub", ".", "◇"))
        reason_role = ""
        where = j["nodes"]
        if j["state"] == "PD":
            reason_role = REASON_ROLE.get(vocab.reason_class(j["reason"]), "sub")
            where = (
                f"({j['reason']}: {j['reason_detail']})"
                if detail and j["reason_detail"]
                else f"({j['reason']})"
            )
        limit = (
            t.g["inf"]
            if j["limit_unlimited"]
            else (_clock(j["limit_minutes"] * 60) if j["limit_minutes"] else dot)
        )
        cells = [
            j["label"], f"{ascii_mark if t.ascii else mark} {j['state']}", j["name"], j["user"],
            j["partition"] or dot, j["qos"] or dot, str(j["node_count"]), where, str(j["cpus"]), j["gres"] or dot,
            _clock(j["elapsed"]) if j["holds"] and j["elapsed"] else dot,
            limit, str(j["priority"]),
        ]  # fmt: skip
        rows.append(
            (j, [render.trunc(c, maxw[i], t.g["ell"]) for i, c in enumerate(cells)], role, reason_role)
        )

    w = [max([width(h)] + [width(r[1][c]) for r in rows]) for c, h in enumerate(heads)]
    show = [x == 0 for x in want]
    # Room permitting, add the optional columns at natural width, cheapest first, and stop
    # at the first that does not fit: a wider window only ever adds columns.
    for rank in sorted({x for x in want if x}):
        c = want.index(rank)
        if 2 + sum(w[i] + 1 for i in range(len(w)) if show[i]) + w[c] + 1 > t.cols:
            break
        show[c] = True
    # No shown column is taken away: room comes from the free-text ones, the comfortable
    # floor first, then the hard one. Past that the table is what it is.
    while 2 + sum(w[i] + 1 for i in range(len(w)) if show[i]) > t.cols:
        cands = [c for c in range(len(w)) if show[c] and flex[c] and w[c] > softw[c]] or [
            c for c in range(len(w)) if show[c] and flex[c] and w[c] > minw[c]
        ]
        if not cands:
            break
        w[min(cands, key=lambda c: flex[c])] -= 1

    cols = [c for c in range(len(heads)) if show[c]]
    headers = [short.get(c, heads[c]) if width(heads[c]) > w[c] else heads[c] for c in cols]
    g = Grid(t, headers, align=[align[c] for c in cols], maxw=[w[c] for c in cols], header_rule=not group)
    counts, order, mine_count = {}, [], 0
    prev = None
    for j, cells, role, reason_role in rows:
        rank = vocab.job_rank(j["state"])
        if group and rank != prev:
            label, style = {0: ("PENDING", "warn"), 1: ("RUNNING", "ok")}.get(rank, ("OTHER", "sub"))
            g.divider(f"{label} ({sum(1 for r in rows if vocab.job_rank(r[0]['state']) == rank)})", style)
        prev = rank
        if j["state"] not in counts:
            order.append(j["state"])
        counts[j["state"]] = counts.get(j["state"], 0) + 1
        own = j["user"] == me
        mine_count += own
        # One tone per row from two axes: whose job it is, and whether it has started.
        step = (0 if own else 1) + (rank == 0)
        base, dim = t.tone(step), t.tone(step + 1)
        styled = []
        for c in cols:
            text, h = cells[c], heads[c]
            if text == t.g["dot"]:
                style = "faint"  # a placeholder is the absence of a value
            elif h == "ST":
                style = role
            elif h == "NAME":
                style = t.c[base] + t.bold if own else base
            elif h == "USER":
                style = "self" if own else base
            elif h == "NODE(REASON)":
                style = reason_role or base
            elif h in ("JOBID", "TIME"):
                style = base
            else:
                style = dim
            styled.append((text, style))
        g.row(styled, gutter=(t.g["self"], "self") if own else "")

    tally = []
    if not rows:
        tally = [("none of yours" if only_mine else "queue empty", "faint")]
    elif group:
        tally = [(f"{len(rows)} job{'s' * (len(rows) != 1)}", "sub")]
    else:
        tally = [(f"{counts[s]} {STATE_LABEL.get(s, s)}", JOB_STYLE.get(s, ("sub",))[0]) for s in order]
    if mine_count and not only_mine:
        tally.append((f"{mine_count} yours", "self"))
    if not rows:
        return t.section("JOBS", 40, tally) + "\n"
    return g.render("JOBS", tally) + "\n"


def show_users(t, model):
    users, gl = model["users"], model["gres"].upper()
    if not users:
        return t.section("USERS", 40, [("nobody", "faint")]) + "\n"
    cap = model["pools"]["cluster"]["gres_total" if model["has_gres"] else "cpu_total"]
    heads = ["USER", "RUN", "PEND", "NODES", "CPU", "MEMORY"] + ([gl] if model["has_gres"] else [])
    g = Grid(
        t, heads, align=["l", "r", "r", "r", "r", "r", "r"], maxw=[16], tail_header="SHARE", tail_width=15
    )

    def num(n, style):
        return (str(n), style if n else "faint")

    tot = dict.fromkeys(("running", "pending", "cpu", "mem", "gres"), 0)
    for u in users:
        own = u["user"] == model["mine"]
        name_style = t.c["self"] + t.bold if own else ("text" if u["running"] else "muted")
        share = u["gres"] if model["has_gres"] else u["cpu"]
        pct = (100 * share + cap // 2) // cap if cap else 0
        tail = t.bar(share, cap, 10, "self" if own else "part") + " " + t.paint("sub", f"{pct:>3}%")
        cells = [(u["user"], name_style), num(u["running"], "ok"), num(u["pending"], "warn"), num(u["nodes"], "text"),
                 num(u["cpu"], "text"), (f"{(u['mem'] + 512) // 1024}G", "text" if u["mem"] else "faint")]  # fmt: skip
        if model["has_gres"]:
            cells.append(num(u["gres"], t.c["text"] + t.bold))
        g.row(cells, gutter=(t.g["self"], "self") if own else "", tail=tail)
        for k in tot:
            tot[k] += u[k]
    g.rule()
    s = t.c["sub"] + t.bold
    total = [("total", s), (str(tot["running"]), s), (str(tot["pending"]), s), (str(model["user_nodes"]), s),
             (str(tot["cpu"]), s), (f"{(tot['mem'] + 512) // 1024}G", s)]  # fmt: skip
    if model["has_gres"]:
        total.append((str(tot["gres"]), s))
    g.row(total)
    return g.render("USERS", [(f"{len(users)} user{'s' * (len(users) != 1)}", "sub")]) + "\n"


def show_why(t, model, detail=False):
    rows = model["why"]
    if not rows:
        return t.section("WHY", 40, [("nothing pending", "faint")]) + "\n"
    gl = model["gres"].upper()
    heads = (
        ["REASON"]
        + (["USER"] if detail else [])
        + ["JOBS", "NODES", "CPU"]
        + ([gl] if model["has_gres"] else [])
    )
    g = Grid(t, heads, align=["l"] + (["l"] if detail else []) + ["r"] * 4, maxw=[34, 16] if detail else [34])

    def total(items):
        return {k: sum(i[k] for i in items) for k in ("jobs", "nodes", "cpu", "gres")}

    def weight(item):
        return (-item[1]["gres"], -item[1]["nodes"], -item[1]["jobs"], item[0])

    def row(prefix, label, label_style, figures, num_style, user="", user_style=""):
        cells = [(label, label_style)] + ([(user, user_style)] if detail else [])
        for k in ("jobs", "nodes", "cpu") + (("gres",) if model["has_gres"] else ()):
            cells.append((str(figures[k]), num_style if figures[k] else "faint"))
        g.row(cells, prefix=prefix, gutter=(t.g["self"], "self") if user and user == model["mine"] else "")

    families = {}
    for r in rows:
        families.setdefault(r["family"], []).append(r)
    for fam, fam_rows in sorted(((f, total(rs)) for f, rs in families.items()), key=weight):
        style = REASON_ROLE.get(fam, "sub")
        row("", vocab.REASON_TITLE[fam], t.c[style] + t.bold, fam_rows, t.c["text"] + t.bold)
        if fam in vocab.REASON_HELP:
            g.note(vocab.REASON_HELP[fam])
        codes = {}
        for r in families[fam]:
            codes.setdefault(r["code"], []).append(r)
        code_list = sorted(((c, total(rs)) for c, rs in codes.items()), key=weight)
        for i, (code, figs) in enumerate(code_list):
            last = i == len(code_list) - 1
            row(t.g["end"] if last else t.g["tee"], code, style, figs, "text")
            if not detail:
                continue
            if model["why_details"].get(code):
                g.note("   " + model["why_details"][code])
            leaves = sorted(((f"{r['partition']}\x00{r['user']}", r) for r in codes[code]), key=weight)
            for k, (_, leaf) in enumerate(leaves):
                tee = t.g["end"] if k == len(leaves) - 1 else t.g["tee"]
                own = leaf["user"] == model["mine"]
                row((t.g["gap"] if last else t.g["pipe"]) + tee, leaf["partition"], "sub", leaf, "sub",
                    leaf["user"], t.c["self"] + t.bold if own else "text")  # fmt: skip
    g.rule()
    s = t.c["sub"] + t.bold
    row("", "total", s, total(rows), s)
    sums = total(rows)
    tally = [(f"{sums['jobs']} job{'s' * (sums['jobs'] != 1)} waiting", "warn")]
    if model["has_gres"] and sums["gres"]:
        tally.append((f"{sums['gres']} {model['gres']} requested", "sub"))
    return g.render("WHY", tally) + "\n"


def show_resources(t, model, view="grouped", split=False, detail=False):
    if not model["nodes"]:
        return "  " + t.paint("warn", "No node data (run on a Slurm client with scontrol access).") + "\n"
    rows = resource_rows(model, view, split, t.g)
    gl, has_gres = model["gres"].upper(), model["has_gres"]
    real = [r for p, r in (x for x in rows if x)]

    def gib(mib):
        return (mib + 512) // 1024

    widths = {
        "cpu": (max(len(str(r["cpu_used"])) for r in real), max(len(str(r["cpu_total"])) for r in real)),
        "mem": (
            max(len(str(gib(r["mem_used"]))) for r in real),
            max(len(f"{gib(r['mem_total'])}G") for r in real),
        ),
        "gres": (max(len(str(r["gres_used"])) for r in real), max(len(str(r["gres_total"])) for r in real)),
    }

    def figure(used, total, key, unit, barw):
        uw, tw = widths[key]
        pieces = [(f"{used:>{uw}}", t.load(used, total)), (f"/{f'{total}{unit}':<{tw}}", "muted")]
        return pieces + ([(" ", "")] + t.bar_pieces(used, total, barw) if barw else [])

    def count(n, style):
        return ("", "") if n is None else (str(n), style if n else "faint")

    def grid(barw):
        heads = ["NAME", "STATE", "CPU", "MEMORY"] + ([gl] if has_gres else []) + ["IDLE", "OFF", "FREE"]
        g = Grid(t, heads, align=["l", "l", "l", "l"] + (["l"] if has_gres else []) + ["r", "r", "r"],
                 maxw=[NAME_MAX, STATE_MAX])  # fmt: skip
        for item in rows:
            if item is None:
                g.rule()
                continue
            prefix, r = item
            kind = r["kind"]
            name_style = {"cluster": t.c["text"] + t.bold, "family": t.c["part"] + t.bold}.get(kind)
            if kind == "part":
                name_style = "part" if prefix else t.c["part"] + t.bold
            elif kind == "node":
                name_style = t.c["node"] + t.bold if r.get("mine") else "node"
            state = r["state"] if detail else r["class"]
            cells = [(r["name"], name_style), (state, NODE_ROLE.get(r["class"], "sub")),
                     figure(r["cpu_used"], r["cpu_total"], "cpu", "", barw),
                     figure(gib(r["mem_used"]), gib(r["mem_total"]), "mem", "G", barw)]  # fmt: skip
            if has_gres:
                cells.append(figure(r["gres_used"], r["gres_total"], "gres", "", barw))
            cells += [count(r["idle"], "ok"), count(r["off"], "hot"), count(r["free"], "ok")]
            g.row(cells, prefix=prefix, gutter=(t.g["self"], "self") if r.get("mine") else "")
        return g

    nbars = 3 if has_gres else 2
    fixed = grid(0).total()
    barw = min(BAR, (t.cols - fixed) // nbars - 1)
    barw = barw if barw >= BAR_MIN else 0
    c = model["pools"]["cluster"]
    occ = (c["occupancy"] // c["nodes"] + 5) // 10 if c["nodes"] else 0
    tally = [(f"{len(model['nodes'])} node{'s' * (len(model['nodes']) != 1)}", "sub")]
    if c["off"]:
        tally.append((f"{c['off']} off", "hot"))
    tally.append((f"{occ}% in use", "sub"))
    out = ""
    if model["gres_pinned"] and not has_gres:
        out = (
            "  "
            + t.paint("warn", f"no node advertises gres/{model['gres']}; FREE counts cpu instead.")
            + "\n\n"
        )
    return out + grid(barw).render("RESOURCES", tally) + "\n"


def privacy_note(t, model):
    """Every allocated core and GRES belongs to some job. When the visible jobs hold far
    less, Slurm is hiding the rest (PrivateData=jobs) and job-derived figures are partial."""
    c, short = model["pools"]["cluster"], []
    if model["has_gres"] and c["gres_used"] and 100 * model["held_gres"] < 95 * c["gres_used"]:
        short.append(f"{model['held_gres']} of {c['gres_used']} allocated {model['gres']}")
    if c["cpu_used"] and 100 * model["held_cpu"] < 95 * c["cpu_used"]:
        short.append(f"{model['held_cpu']} of {c['cpu_used']} allocated cpu")
    if not short:
        return ""
    return (
        "  "
        + t.paint("hot", f"! only {' and '.join(short)} belong to jobs you can see (PrivateData);")
        + "\n"
        "    "
        + t.paint("hot", "USERS, WHY and shared pools (counted from jobs) are partial; node figures are not.")
        + "\n\n"
    )


def show(t, model, args):
    parts = []
    if args.queue != "none":
        parts.append(show_jobs(t, model, args.group, args.detail, args.queue))
    if args.users:
        parts.append(show_users(t, model))
    if args.why:
        parts.append(show_why(t, model, args.detail))
    if args.resources:
        parts.append(show_resources(t, model, args.view, args.split, args.detail))
    if args.users or args.why or args.resources:
        parts.append(privacy_note(t, model))
    return "\n".join(p for p in parts if p)


def to_json(model, args):
    """The model as JSON: the tables' figures, and the resource tree in full."""
    plain = dict.fromkeys(("end", "tee", "gap", "pipe"), "")
    resources = [item[1] for item in resource_rows(model, "grouped", True, plain) if item]
    return {
        "cluster": model["cluster"], "gres": model["gres"], "free": model["free_what"],
        "jobs": model["jobs"], "users": model["users"], "pending": model["why"],
        "resources": [{k: v for k, v in r.items() if k not in ("occupancy", "partitions")} for r in resources],
    }  # fmt: skip


# --- Running ------------------------------------------------------------------------


def _me():
    return os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name


def snapshot(args):
    """One consistent read: three controller calls."""
    nodes, parts, jobs = slurm.load("nodes"), slurm.load("partitions"), slurm.load("jobs")
    me = _me()
    sel = Filters(me if args.me else "", args.user, args.partition, args.nodelist, args.states, args.jobs,
                  args.account, args.qos, args.name)  # fmt: skip
    return build(nodes, parts, jobs, sel, me, args.gres, title=os.environ.get("CLUSTER_TITLE"))


def frame(theme, args):
    model = snapshot(args)
    return show(theme, model, args), 1 if not model["nodes"] else 0


def watch(theme, args):
    """Redraw every interval; back off while Slurm fails or does not answer, so a
    struggling or throttling slurmctld is not asked again at full rate."""
    wait = args.interval
    try:
        while True:
            if sys.stdout.isatty() and not args.wide:
                theme.cols = shutil.get_terminal_size((theme.cols, 24)).columns
            try:
                text, _ = frame(theme, args)
                wait, pace = args.interval, f"every {args.interval}s"
            except slurm.FAILURES as e:
                text = "  " + theme.paint("crit", f"{e.cmd[0]}: {slurm.failure(e)}") + "\n"
                wait = min(wait * 2, MAX_BACKOFF)
                pace = theme.c["warn"] + f"Slurm not answering, next try in {wait}s"
            except slurm.SchemaError as e:
                text = "  " + theme.paint("crit", str(e)) + "\n"
                wait = min(wait * 2, MAX_BACKOFF)
                pace = theme.c["warn"] + f"next try in {wait}s"
            if sys.stdout.isatty():
                sys.stdout.write("\033[H\033[2J")
            footer = theme.paint("faint", f"{time.strftime('%H:%M:%S')}   {pace}, Ctrl-C to stop")
            sys.stdout.write(f"{text}\n  {footer}\n")
            sys.stdout.flush()
            time.sleep(wait)
    except KeyboardInterrupt:
        sys.stdout.write(theme.reset + "\n")
        return 0


def main(argv=None):
    def body():
        args, theme = COMMAND.parse(argv)
        if args.interval and not args.json:
            return watch(theme, args)
        if args.json:
            model = snapshot(args)
            json.dump(to_json(model, args), sys.stdout, indent=1, default=str)
            sys.stdout.write("\n")
            return 0 if model["nodes"] else 1
        text, rc = frame(theme, args)
        sys.stdout.write(text)
        return rc

    return run_main("sq", body)


if __name__ == "__main__":
    sys.exit(main())
