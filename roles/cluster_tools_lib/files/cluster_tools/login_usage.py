"""usage login-node: your CPU, memory and tasks on this login node against the limits
every person's session gets here (cluster_tools_user_slice_limits, the user slice's
cgroup): memory and tasks now, CPU over one second. Past a limit the node slows you
down (CPU) or refuses (memory, new processes). Python 3.9 or newer, standard library only.
"""

import sys

from . import rules
from .usage import JSON_NOTE
from .cli import UNKNOWN, Command, opt_json, opt_user, run_main

COMMAND = Command(
    "usage login-node",
    "your CPU, memory and tasks on this login node against the per-person limits",
    [
        opt_user("someone else's figures instead of yours", metavar="NAME"),
        opt_json("one object, schema 1 (below)"),
    ],
    usage="usage login-node [OPTIONS]",
    machine="--json",
    notes=[JSON_NOTE],
    summary_ko="이 로그인 노드에서 내 CPU, 메모리, 프로세스 수와 한 사람당 한도",
)


def main(argv=None):
    from .usage import context, emit, table

    args, theme = COMMAND.parse(argv)

    def body():
        user, uid, cfg, _ = context(args.user)
        found = [rules.login(user, cfg, uid)]
        return emit(
            "usage login-node", user, found, args, theme, table(found, theme, f"{user} on this login node")
        )

    return run_main("usage login-node", body, unknown=UNKNOWN)


if __name__ == "__main__":
    sys.exit(main())
