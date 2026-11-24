"""qos: the QoS levels you can submit against: what each one preempts, what preempts
it, and how full its shared quota is right now.

build() turns Slurm's JSON into a model and show() draws it; --json prints the model
itself, so a script reads the same figures a person sees.
"""

import json
import sys

from . import slurm, vocab
from .cli import Command, Opt, me, opt_all, opt_json, opt_me, opt_user, run_main
from .render import Grid, duration, width

BAR = 6  # quota bar width; shrinks to fit, then goes
BAR_MIN = 3
NAME_MAX = 20

NOTES = [
    "A QoS decides three things about a job: where it sits in the queue (Priority), what\n"
    "it may push aside to start (Preempt), and how much of the cluster the whole QoS may\n"
    "hold at once (GrpTRES, a limit on the SUM of every running job using it, not per\n"
    "user). The last is why a job can wait on QOSGrpGRES while the cluster looks idle.",
    "GPU columns appear for every GPU type some QoS limits, and EXEMPT and GRACE only\nonce a QoS sets them.",
    "Reading the table:\n"
    "  A GPU figure is the QoS's usage against its quota: every job on that QoS that\n"
    "  holds its allocation, the sum the quota is checked against. -/N is unknown usage.\n"
    "  Under PrivateData=jobs only your own jobs are visible, so usage reads low.\n"
    "  The any column is a quota on GPUs of every type together (gres/gpu=N).\n"
    "  A limit of 0 is not a full quota: that QoS may not use that GPU type at all.\n"
    "  No MAX WALL means no QoS limit; the partition's own MaxTime still applies (sq -P).\n"
    "  A job pending on QOSGrpGRES is waiting for its own QoS's quota, not for the cluster.",
]

COMMAND = Command(
    "qos",
    "the QoS levels you can submit against",
    [
        opt_me("only the QoS you may submit against", renamed=("--mine",)),
        opt_all("include QoS held by no user (Slurm's built-in normal)"),
        opt_user("answer for someone else instead of you", metavar="NAME"),
        Opt(
            "--usage",
            negate=True,
            default=True,
            help="the live usage figures (--no-usage skips the job list)",
        ),
        opt_json("the model as JSON, for scripts (ignores $QOS_OPTIONS)"),
    ],
    env="QOS_OPTIONS",
    machine="--json",
    notes=NOTES,
    theme_env="QOS_THEME",
    summary_ko="내가 잡을 낼 수 있는 QoS",
)


ANY = "any"  # the column of a quota on GPUs of every type together


def _gpu_type(tres):
    """The GPU column a TRES name counts in: its type, ANY for plain gres/gpu, None for
    what is not a GPU."""
    if tres == "gres/gpu":
        return ANY
    return tres[len("gres/gpu:") :] if tres.startswith("gres/gpu:") else None


def gpu_limits(qos):
    """{gpu column: limit} from the QoS's GrpTRES: the one reading of a QoS's GPU quota,
    for this command and sq's TEAMS."""
    total = slurm.tres_list(((qos.get("limits") or {}).get("max") or {}).get("tres", {}).get("total"))
    return {_gpu_type(k): v for k, v in total.items() if _gpu_type(k)}


def gpu_used(jobs_data):
    """{(QoS, gpu column): GPUs its jobs hold now}: the one count of a quota's live use,
    for this command and sq's TEAMS."""
    used = {}
    for j in (jobs_data or {}).get("jobs", []):
        if not vocab.job_holds(j.get("job_state")):
            continue
        for key, n in slurm.tres_str(j.get("tres_alloc_str")).items():
            if _gpu_type(key):
                k = (j.get("qos", ""), _gpu_type(key))
                used[k] = used.get(k, 0) + int(n)
    return used


def quota_figure(t, used, limit):
    """A quota as text: USED/LIMIT. A limit of 0 is 0, barred; no limit at all is "-";
    usage not read is a dot, never 0 (0/8 would read as free)."""
    return f"{t.g['dot'] if used is None else used}/{'-' if limit is None else limit}"


def quota_cell(t, used, limit, bar, figure_width=0):
    """One quota as a Grid cell, the same shape in every row and every command: its
    figure (quota_figure), right-aligned to FIGURE_WIDTH so a column's bars line up,
    then a BAR-column trough filled by the load. Faint unless a limit above 0 gives the
    load its colour (or GPUs are held past a limit of 0); a limit of 0 or none is an
    empty trough."""
    shown = quota_figure(t, used, limit)
    if used is None or limit is None:
        style = "faint"
    elif limit == 0:
        style = "crit" if used else "faint"  # held past a barred quota (lowered since)
    else:
        style = t.load(used, limit)
    piece = [(" " * max(0, figure_width - width(shown)) + shown, style)]
    if bar:
        trough = [(t.g["bar_e"] * bar, "rule")]
        piece += [(" ", "")] + (t.bar_pieces(used, limit, bar) if limit and used is not None else trough)
    return piece


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

    used = gpu_used(jobs_data) if usage else {}  # (qos, gpu column) -> GPUs held by its jobs

    qos_list = qos_data.get("qos", [])
    preempted_by = {}
    for q in qos_list:
        for victim in (q.get("preempt") or {}).get("list") or []:
            preempted_by.setdefault(victim, []).append(q["name"])
    types = sorted({t for q in qos_list for t in gpu_limits(q)})

    rows, hidden = [], 0
    for q in qos_list:
        name = q["name"]
        if mine_only and name not in mine:
            continue
        if not show_all and name not in granted:
            hidden += 1
            continue
        limits = q.get("limits") or {}
        # Every GPU column, a quota or not, so each row has the same cells; None: no limit.
        lims = gpu_limits(q)
        gpu = {t: {"used": used.get((name, t), 0) if usage else None, "limit": lims.get(t)} for t in types}
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


def _table(t, model, bar, caps):
    rows, types = model["qos"], model["gpu_types"]
    exempt = any(r["exempt_seconds"] for r in rows)
    grace = any(r["grace_seconds"] for r in rows)
    headers = ["NAME", "PRIORITY", "PREEMPTS", "PREEMPTED BY", *types, "MAX WALL"]
    headers += ["EXEMPT"] * exempt + ["GRACE"] * grace
    align = ["l", "r", "l", "l", *["l"] * len(types), "l"] + ["r"] * (exempt + grace)
    g = Grid(t, headers, align=align, maxw=[NAME_MAX, 8, caps[0], caps[1]])
    dot = t.g["dot"]
    figures = {
        ty: max(width(quota_figure(t, r["gpu"][ty]["used"], r["gpu"][ty]["limit"])) for r in rows)
        for ty in types
    }
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
            q = r["gpu"][ty]
            cells.append(quota_cell(t, q["used"], q["limit"], bar, figures[ty]))
        cells.append(plain(duration(r["max_wall_seconds"], short=False) if r["max_wall_seconds"] else dot))
        if exempt:
            cells.append(plain(duration(r["exempt_seconds"], short=False) if r["exempt_seconds"] else dot))
        if grace:
            cells.append(plain(duration(r["grace_seconds"], short=False) if r["grace_seconds"] else dot))
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
    if model["hidden"]:
        tally.append((f"{model['hidden']} hidden (--all)", "faint"))
    out = [g.render("QoS", tally), ""]

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
    return "\n".join(out).rstrip() + "\n\n"


def main(argv=None):
    def body():
        args, theme = COMMAND.parse(argv)
        qos_data, assoc_data = slurm.load("qos"), slurm.load("associations")
        # A failed usage read leaves usage unknown and says why, rather than failing qos.
        jobs, usage_error = None, ""
        if args.usage:
            try:
                jobs = slurm.load("jobs")
            except (slurm.Failed, slurm.SchemaError) as e:
                usage_error = str(e)
        usage = args.usage and not usage_error
        model = build(qos_data, assoc_data, jobs, args.user or me(), usage, args.me, args.all)
        model["usage_error"] = usage_error
        if args.json:
            json.dump(model, sys.stdout, indent=1)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(show(theme, model, args.me))

    return run_main("qos", body)


if __name__ == "__main__":
    sys.exit(main())
