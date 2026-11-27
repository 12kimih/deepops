"""The exit status of every cluster command, and the two failures that set it.

One convention, so a script or an agent reads any command the same way, as monitoring
tools do: 0 ok; 1 at risk, only from a command that judges (usage and its
subcommands); 2 could not run, where the same call again will fail again; 3 a source
(Slurm, Prometheus, a file) did not answer, retry later. cli.run_main ends a command
on a raised CannotRun or NoAnswer with its `status`, and NOTE words them for each
command's help. A failure of both kinds (a missing Slurm client is a Slurm failure that
waiting will not cure) has the status of its first base.
Python 3.9 or newer, standard library only.
"""

OK, AT_RISK, CANNOT_RUN, UNKNOWN = 0, 1, 2, 3


class CannotRun(Exception):
    """What was asked cannot be done here: a wrong option or request, a broken
    config, a Slurm client not installed. str() is one line on why."""

    status = CANNOT_RUN


class NoAnswer(Exception):
    """A source gave no usable answer: it failed, was silent past its timeout, or
    replied with something unreadable. str() is one line on why."""

    status = UNKNOWN


def note(judges=False):
    """The exit statuses as a command's help states them; JUDGES adds 1 (at risk)."""
    lines = [
        "Exit status:",
        f"  {OK}  ok",
        *([f"  {AT_RISK}  at risk: a rule at warn or act"] if judges else []),
        f"  {CANNOT_RUN}  could not run: a wrong option, a broken config, no Slurm client here",
        f"  {UNKNOWN}  no answer from Slurm, Prometheus or a file: retry later",
    ]
    return "\n".join(lines)
