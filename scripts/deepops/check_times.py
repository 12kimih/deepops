#!/usr/bin/env python3
"""The order scripts/check.sh starts its steps in: longest first.

    check_times.py order TIMES    step names on stdin, one a line; their indexes on
                                  stdout, longest last time first, unknown ones first
    check_times.py record TIMES   "SECONDS<TAB>NAME" lines on stdin: this run's times,
                                  kept over TIMES' others

A run takes as long as its last step to end. In the order the steps are declared, a
nine-minute step listed late ran past all the others (794s for 540s of work per CPU,
2026-11-26); started first, it runs beside them. TIMES (one "SECONDS<TAB>NAME" a line;
check.sh keeps it in the clone's shared git directory, for all its worktrees) is a
cache: missing or unreadable, every step is unknown and the order is the declared
one. A step never met before is assumed long, so a new slow step cannot end the run
late. Python 3.9 or newer, standard library only.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402

UNKNOWN = float("inf")


def load(path):
    times = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                secs, _, name = line.rstrip("\n").partition("\t")
                if name and secs.isdigit():
                    times[name] = int(secs)
    except OSError:
        pass
    return times


def order(names, times):
    """Indexes of NAMES, longest known time first, unknown first of all, ties declared."""
    return sorted(range(len(names)), key=lambda i: (-times.get(names[i], UNKNOWN), i))


def record(path, new):
    """TIMES with NEW ({name: seconds}) over it, written whole (a reader never sees half)."""
    times = {**load(path), **new}
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".check-times.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.writelines(f"{secs}\t{name}\n" for name, secs in sorted(times.items()))
    os.replace(tmp, path)


def main(argv):
    parser = cli.parser(__doc__)
    parser.add_argument("action", choices=("order", "record"))
    parser.add_argument("times", metavar="TIMES")
    args = parser.parse_args(argv)
    path = args.times
    lines = sys.stdin.read().splitlines()
    if args.action == "order":
        print("\n".join(str(i) for i in order(lines, load(path))))
    else:
        new = {}
        for line in lines:
            secs, _, name = line.partition("\t")
            if name and secs.isdigit():
                new[name] = int(secs)
        record(path, new)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
