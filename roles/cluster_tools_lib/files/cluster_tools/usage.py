"""usage: where you stand against every rule that warns, limits or cancels, with the
numbers the enforcers use (cluster_tools.rules: each rule measured by its enforcer's own
code, against the thresholds the cluster_tools role renders for this node).

    usage                  every rule, one line each (this module)
    usage gpu [JOB]        what your jobs' GPUs did (cluster_tools.gpu_usage)
    usage disk             your space and files against the limits, folders worth
                           cleaning, scratch files due to go (cluster_tools.disk_usage)
    usage io               your jobs' and logins' I/O, the node's share of the shared
                           storage, a notice due (cluster_tools.io_load)
    usage submit           your queued jobs against the cap, and submit holds
                           (cluster_tools.submit_usage)
    usage login-node       your CPU, memory and tasks on this login node against the
                           per-person limits (cluster_tools.login_usage)

One dispatcher (cluster_tools.cli Group); each subcommand keeps its own domain's code,
and every one shares --json (schema 1, rules.JSON_NOTE), -u/--user and the exit status:
0 ok, 1 at risk, 2 could not run, 3 unknown. Python 3.9 or newer, standard library only.
"""

import json
import os
import pwd
import sys
import time

from . import rules
from .cli import UNKNOWN, Command, Group, me, opt_json, opt_user, run_main
from .prometheus import Prometheus
from .render import Grid
from .site import info as site_info

# The --json every subcommand documents in its --help (rules.document writes it).
JSON_NOTE = (
    "--json: one object, schema 1: schema (1), command (usage, usage disk, ...), user, at\n"
    "(Unix time), status (the exit status below), rules (one object per rule shown:\n"
    "rule, title, mine, limit, state, when, check, detail) and the subcommand's own figures\n"
    "under its own keys. A key is only ever added within a schema.\n"
    "Exit status: 0 ok, 1 at risk (a rule at warn or act), 2 could not run (a wrong\n"
    "option, a broken config), 3 unknown (Slurm, Prometheus or a file did not answer):\n"
    "retry later."
)


GROUP = Group(
    "usage",
    "cluster_tools.usage",
    {
        "gpu": "cluster_tools.gpu_usage",
        "disk": "cluster_tools.disk_usage",
        "io": "cluster_tools.io_load",
        "submit": "cluster_tools.submit_usage",
        "login-node": "cluster_tools.login_usage",
    },
)


def subcommands_note():
    lines = ["Subcommands (each with its own -h):"]
    for sub, command in GROUP.commands()[1:]:
        lines.append(f"  {sub:<11} {command.summary}")
    return "\n".join(lines)


NOTES = [
    subcommands_note,
    "States: ok; warn: past a warning level; act: the rule acts on you now or at the time\n"
    "shown (a notice, a cancel, a hold, a removal); off: the cluster does not run it; ?:\n"
    "its source (Slurm, Prometheus, the scratch space) did not answer. Each line names\n"
    "the subcommand that shows its detail.",
    JSON_NOTE,
]

COMMAND = Command(
    "usage",
    "where you stand against every rule that warns, limits or cancels",
    [
        opt_user("someone else's figures instead of yours", metavar="NAME"),
        opt_json("one object, schema 1 (below)"),
    ],
    usage="usage [gpu|disk|io|submit|login-node] [OPTIONS]",
    machine="--json",
    notes=NOTES,
    summary_ko="경고, 한도, 취소가 있는 규칙마다 내 현황",
)

STYLE = {"ok": "text", "warn": "warn", "act": "crit", "off": "muted", "?": "warn"}


def context(user=None):
    """(user, uid, rules config, Prometheus or None) for a usage subcommand."""
    user = user or me()
    try:
        uid = pwd.getpwnam(user).pw_uid
    except KeyError:
        uid = os.getuid()
    cfg = rules.load_config(os.environ.get("CLUSTER_TOOLS_USAGE_CONFIG", rules.PATH))
    url = cfg.get("prometheus_url") or site_info().get("prometheus_url")
    return user, uid, cfg, Prometheus(url) if url else None


def table(rows, theme, title):
    """The rules' rows as a table under TITLE."""
    grid = Grid(theme, ["RULE", "MINE", "LIMIT", "STATE", "WHEN", "CHECK"], align=["l"] * 6)
    for r in rows:
        grid.row([(r.title, "text"), (r.mine, "text"), (r.limit, "muted"), (r.state, STYLE[r.state]),
                  (r.when, STYLE[r.state] if r.state != "ok" else "muted"), (r.check if r.state != "off" else "", "muted")])  # fmt: skip
    return grid.render(title, [("the numbers the rules act on", "sub")]) + "\n"


def emit(command, user, rows, args, theme, text, **figures):
    """Print the --json document or TEXT; the exit status of ROWS."""
    if args.json:
        print(
            json.dumps(
                rules.document(command, user, rows, time.time(), **figures), indent=2, ensure_ascii=False
            )
        )
    else:
        sys.stdout.write(text)
    return rules.exit_status(rows)


def overview_main(argv=None):
    args, theme = COMMAND.parse(argv)

    def body():
        user, uid, cfg, prom = context(args.user)
        rows = rules.overview(user, cfg, uid=uid, prom=prom)
        return emit("usage", user, rows, args, theme, table(rows, theme, f"usage of {user}"))

    return run_main("usage", body, unknown=UNKNOWN)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in GROUP.subs or argv[:2] == ["--completion", "bash"]:
        return GROUP.main(argv)
    return overview_main(argv)


if __name__ == "__main__":
    sys.exit(main())
