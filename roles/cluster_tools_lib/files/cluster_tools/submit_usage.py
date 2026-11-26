"""usage submit: whether a job of yours would be refused now, and why: your queued and
running jobs against the cap Slurm holds you to (MaxSubmitJobs), a submit hold at a
disk limit (cluster_tools.submit_holds, as the disk report judges it), and your
membership's end (the holds from the day after it). Each from its enforcer's own code
(cluster_tools.rules). Python 3.9 or newer, standard library only.
"""

import sys
import time

from . import rules, slurm
from .usage import JSON_NOTE
from .cli import UNKNOWN, Command, opt_json, opt_user, run_main

COMMAND = Command(
    "usage submit",
    "whether a new job of yours would be refused: the queued-job cap and the submit holds, with why",
    [
        opt_user("someone else's figures instead of yours", metavar="NAME"),
        opt_json("one object, schema 1 (below)"),
    ],
    usage="usage submit [OPTIONS]",
    machine="--json",
    notes=[JSON_NOTE],
    summary_ko="새 잡이 거부될지와 그 이유: 낼 수 있는 잡 수, 제출 보류",
)


def rows(user, cfg, prom, now, load=slurm.load):
    """The queue, the disk hold and the membership end, in that order."""
    out = []
    try:
        out.append(rules.queued(user, load("jobs_detail"), load("associations")))
    except slurm.Error as e:
        out.append(rules.unknown("queued-jobs", str(e)))
    disk = rules.disk(user, cfg, prom, now) if prom else rules.unknown("disk", "no prometheus_url")
    if disk.state == "warn":  # past a warning level only: no hold
        disk = rules.row("disk", disk.mine, disk.limit, "ok", disk.when)
    out += [disk, rules.membership(user, cfg, now)]
    return out


def main(argv=None):
    from .usage import context, emit, table

    args, theme = COMMAND.parse(argv)

    def body():
        user, _, cfg, prom = context(args.user)
        found = rows(user, cfg, prom, time.time())
        return emit("usage submit", user, found, args, theme, table(found, theme, f"submitting as {user}"))

    return run_main("usage submit", body, unknown=UNKNOWN)


if __name__ == "__main__":
    sys.exit(main())
