"""qos: the QoS levels you can submit against: what each one preempts, what preempts
it, and how full its shared quota is right now.

build() turns Slurm's JSON into a model and show() draws it; --json prints the model
itself, so a script reads the same figures a person sees.
"""

import json
import os
import pwd
import sys

from clustertools import slurm
from clustertools.cli import Command, Opt, run_main
from clustertools.render import Grid, width

BAR = 6  # quota bar width; shrinks to fit, then goes
BAR_MIN = 3
NAME_MAX = 20

NOTES = [
    "A QoS decides three things about a job: where it sits in the queue (Priority), what\n"
    "it may push aside to start (Preempt), and how much of the cluster the whole QoS may\n"
    "hold at once (GrpTRES, a limit on the SUM of every running job using it, not per\n"
    "user). The last is why a job can wait on QOSGrpGRES while the cluster looks idle.",
    "GPU columns appear for every GPU type some QoS limits, and EXEMPT and GRACE only\nonce a QoS sets them.",
]

COMMAND = Command(
    "qos",
    "the QoS levels you can submit against",
    [
        Opt("-m", "--mine", brief=True, help="only the QoS you may submit against"),
        Opt("-a", "--all", brief=True, help="include QoS held by no user (Slurm's built-in normal)"),
        Opt(
            "-u",
            "--user",
            metavar="NAME",
            complete="users",
            brief=True,
            help="answer for someone else instead of you",
        ),
        Opt(
            "--usage",
            negate=True,
            default=True,
            help="the live usage figures (--no-usage skips the job list)",
        ),
        Opt("--json", section="Output", help="the model as JSON, for scripts (ignores $QOS_OPTIONS)"),
    ],
    env="QOS_OPTIONS",
    machine="--json",
    notes=NOTES,
    theme_env="QOS_THEME",
)


def _gpu_limits(qos):
    """{gpu type: limit} from the QoS's GrpTRES."""
    total = slurm.tres_list(((qos.get("limits") or {}).get("max") or {}).get("tres", {}).get("total"))
    return {k[len("gres/gpu:") :]: v for k, v in total.items() if k.startswith("gres/gpu:")}


def _dig(d, *path):
    for key in path:
        d = (d or {}).get(key)
    return d


def build(qos_data, assoc_data, jobs_data, who, usage=True, mine_only=False, show_all=False):
    """The model: one entry per QoS shown, with what WHO may use and live usage."""
    mine, granted, default = set(), set(), ""
    for a in assoc_data.get("associations", []):
        names = set(a.get("qos") or [])
        if a.get("user") == who:
            mine |= names
            d = (a.get("default") or {}).get("qos") or ""
            if d and (a.get("is_default") or not default):
                default = d
        if a.get("user") and a.get("user") != "root":
            granted |= names
    if not granted:  # read nothing: filtering on it would hide all
        show_all = True

    used = {}  # (qos, gpu type) -> GPUs held by running jobs
    for j in (jobs_data or {}).get("jobs", []) if usage else []:
        if "RUNNING" not in (j.get("job_state") or []):
            continue
        for key, n in slurm.tres_str(j.get("tres_alloc_str")).items():
            if key.startswith("gres/gpu:"):
                k = (j.get("qos", ""), key[len("gres/gpu:") :])
                used[k] = used.get(k, 0) + int(n)

    qos_list = qos_data.get("qos", [])
    preempted_by = {}
    for q in qos_list:
        for victim in (q.get("preempt") or {}).get("list") or []:
            preempted_by.setdefault(victim, []).append(q["name"])
    types = sorted({t for q in qos_list for t in _gpu_limits(q)})

    rows, hidden = [], 0
    for q in qos_list:
        name = q["name"]
        if mine_only and name not in mine:
            continue
        if not show_all and name not in granted:
            hidden += 1
            continue
        limits = q.get("limits") or {}
        gpu = {
            t: {"used": used.get((name, t), 0) if usage else None, "limit": lim}
            for t, lim in _gpu_limits(q).items()
        }
        wall = slurm.num(_dig(limits, "max", "wall_clock", "per", "job"))
        rows.append(
            {
                "name": name,
                "priority": slurm.num(q.get("priority")) or 0,
                "preempts": sorted((q.get("preempt") or {}).get("list") or []),
                "preempted_by": sorted(preempted_by.get(name, [])),
                "mine": name in mine,
                "gpu": gpu,
                "max_wall_seconds": wall * 60 if wall else None,
                "exempt_seconds": slurm.num((q.get("preempt") or {}).get("exempt_time")) or None,
                "grace_seconds": slurm.num(limits.get("grace_time")) or None,
                "per_user": {
                    "tres": slurm.tres_list(_dig(limits, "max", "tres", "per", "user")),
                    "running_jobs": slurm.num(_dig(limits, "max", "jobs", "active_jobs", "per", "user")),
                    "submitted_jobs": slurm.num(_dig(limits, "max", "jobs", "per", "user")),
                },
            }
        )
    return {
        "user": who,
        "default_qos": default,
        "gpu_types": types,
        "usage": usage,
        "hidden": hidden,
        "qos": rows,
    }


def clock(seconds):
    """[D-]HH:MM:SS, as Slurm writes a duration."""
    days, rest = divmod(int(seconds), 86400)
    hms = f"{rest // 3600:02d}:{rest % 3600 // 60:02d}:{rest % 60:02d}"
    return f"{days}-{hms}" if days else hms


def _table(t, model, bar, caps):
    rows, types = model["qos"], model["gpu_types"]
    exempt = any(r["exempt_seconds"] for r in rows)
    grace = any(r["grace_seconds"] for r in rows)
    headers = ["NAME", "PRIORITY", "PREEMPTS", "PREEMPTED BY", *types, "MAX WALL"]
    headers += ["EXEMPT"] * exempt + ["GRACE"] * grace
    align = ["l", "r", "l", "l", *["l"] * len(types), "l"] + ["r"] * (exempt + grace)
    g = Grid(t, headers, align=align, maxw=[NAME_MAX, 8, caps[0], caps[1]])
    dot = t.g["dot"]
    for r in rows:
        base = "text" if r["mine"] else "sub"

        def plain(value, style=base):
            return (value, style) if value != dot else (dot, "faint")  # absence is not a value

        cells = [
            (r["name"], t.c[base] + t.bold if r["mine"] else base),
            plain(str(r["priority"])),
            plain(",".join(r["preempts"]) or dot),
            plain(",".join(r["preempted_by"]) or dot),
        ]
        for ty in types:
            q = r["gpu"].get(ty)
            if q is None:
                cells.append((dot, "faint"))
                continue
            if q["used"] is None:  # unknown, never zero: 0/8 would read as free
                cells.append([(f"{dot}/{q['limit']}", "faint")])
                continue
            u = q["used"]
            style = "faint" if q["limit"] == 0 else t.load(u, q["limit"])  # 0: barred, not full
            piece = [(f"{u}/{q['limit']}", style)]
            if bar:
                piece += [(" ", "")] + t.bar_pieces(u, q["limit"], bar)
            cells.append(piece)
        cells.append(plain(clock(r["max_wall_seconds"]) if r["max_wall_seconds"] else dot))
        if exempt:
            cells.append(plain(clock(r["exempt_seconds"]) if r["exempt_seconds"] else dot))
        if grace:
            cells.append(plain(clock(r["grace_seconds"]) if r["grace_seconds"] else dot))
        g.row(cells, gutter=(t.g["self"], "self") if r["mine"] else "")
    return g


def show(t, model, only_mine=False):
    rows = model["qos"]
    if not rows:
        return "  " + t.paint("warn", f"{model['user']} may not submit against any QoS.") + "\n"

    # The bars go first: they restate the numbers. Then the two free-text preemption
    # columns narrow, the wider one first.
    bar = BAR if model["gpu_types"] else 0
    natural = [
        max([width(",".join(r[k])) for r in rows] + [len(h)])
        for k, h in (("preempts", "PREEMPTS"), ("preempted_by", "PREEMPTED BY"))
    ]
    caps = list(natural)
    g = _table(t, model, bar, caps)
    while g.total() > t.cols:
        if bar >= BAR_MIN:
            bar = bar - 1 if bar > BAR_MIN else 0
        else:
            wide = max((0, 1), key=lambda i: caps[i])
            if caps[wide] <= len(("PREEMPTS", "PREEMPTED BY")[wide]):
                break
            caps[wide] -= 1
        g = _table(t, model, bar, caps)

    mine = sum(r["mine"] for r in rows)
    tally = [(f"{len(rows)} QoS", "sub")]
    if mine and not only_mine:
        tally.append((f"{mine} yours", "self"))
    out = [g.render("QoS", tally), ""]

    def note(text):
        out.append("  " + t.paint("sub", text))

    if model["default_qos"]:
        out.append(
            f"  {t.paint('self', model['user'])}{t.paint('sub', ' submits as ')}"
            f"{t.paint(t.c['part'] + t.bold, model['default_qos'])}"
            f"{t.paint('sub', ' unless --qos says otherwise.')}"
        )
    for r in rows:
        pu, parts = r["per_user"], []
        if pu["tres"]:
            parts.append("per-user " + ",".join(f"{k}={v}" for k, v in sorted(pu["tres"].items())))
        if pu["running_jobs"]:
            parts.append(f"max {pu['running_jobs']} running/user")
        if pu["submitted_jobs"]:
            parts.append(f"max {pu['submitted_jobs']} submitted/user")
        if parts:
            out.append(f"  {t.paint('part', r['name'])} {t.paint('sub', ', '.join(parts))}")
    if model.get("usage_error"):
        out.append("  " + t.paint("crit", f"live usage unavailable: {model['usage_error']}"))
    note("No MAX WALL means no QoS limit; the partition's own MaxTime still applies (sq -T).")
    if model["usage"]:
        note(
            "Usage is every RUNNING job on that QoS, cluster-wide: the same sum its quota is checked against."
        )
    if any(q["limit"] == 0 for r in rows for q in r["gpu"].values()):
        note("A limit of 0 is not a full quota: that QoS may not use that GPU type at all.")
    if model["hidden"]:
        verb = "is" if model["hidden"] == 1 else "are"
        note(f"{model['hidden']} QoS that no user holds {verb} hidden; --all shows them.")
    note("A job pending on QOSGrpGRES is waiting for its own QoS's quota, not for the cluster.")
    return "\n".join(out) + "\n\n"


def _me():
    return os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name


def main(argv=None):
    def body():
        args, theme = COMMAND.parse(argv)
        qos_data, assoc_data = slurm.load("qos"), slurm.load("associations")
        # A failed usage read leaves usage unknown and says why, rather than failing qos.
        jobs, usage_error = None, ""
        if args.usage:
            try:
                jobs = slurm.load("jobs")
            except slurm.FAILURES as e:
                usage_error = slurm.failure(e)
        usage = args.usage and not usage_error
        model = build(qos_data, assoc_data, jobs, args.user or _me(), usage, args.mine, args.all)
        model["usage_error"] = usage_error
        if args.json:
            json.dump(model, sys.stdout, indent=1)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(show(theme, model, args.mine))

    return run_main("qos", body)


if __name__ == "__main__":
    sys.exit(main())
