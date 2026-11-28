#!/usr/bin/env python3
"""The order scripts/check.sh starts its steps in: longest first.

    check_times.py order TIMES    step names on stdin, one a line; their indexes on
                                  stdout, longest last time first, unknown ones first
    check_times.py record TIMES   "SECONDS<TAB>NAME" lines on stdin: this run's times,
                                  kept over TIMES' others
    check_times.py split TIMES NAME
                                  exit 0 when the step NAME (a test file's) took
                                  SPLIT_SECONDS or more last time, run whole or as its
                                  methods ("NAME minus its closing parenthesis, a dot,
                                  the method"), so check.sh runs its methods side by side

A run takes as long as its last step to end. In the order the steps are declared, a
nine-minute step listed late ran past all the others (794s for 540s of work per CPU,
2026-11-26); started first, it runs beside them. TIMES (one "SECONDS<TAB>NAME" a line;
check.sh keeps it in the clone's shared git directory, for all its worktrees) is a
cache: missing or unreadable, every step is unknown and the order is the declared
one. A step never met before is assumed long, so a new slow step cannot end the run
late.

A test file is one step until it is slow: then each of its methods is one, so the run
is not as long as that file alone (2026-11-28: one file of playbook runs took 261s of a
407s run). Slow is read from the last run, whole or split, so a file split once stays
split while its methods together stay slow, and a test file needs no marker for it.
Python 3.9 or newer, standard library only.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402

UNKNOWN = float("inf")
# A test file whose last run took this long runs as its methods, side by side.
SPLIT_SECONDS = 60


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


def slow(name, times):
    """Whether the test file step NAME ("unit tests (suite/module)") took SPLIT_SECONDS
    or more in its last run: whole, or as its methods together."""
    split = name[:-1] + "."
    total = times.get(name, 0) + sum(t for n, t in times.items() if n.startswith(split))
    return total >= SPLIT_SECONDS


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
    parser.add_argument("action", choices=("order", "record", "split"))
    parser.add_argument("times", metavar="TIMES")
    parser.add_argument("name", nargs="?", metavar="NAME")
    args = parser.parse_args(argv)
    path = args.times
    if args.action == "split":
        return 0 if args.name and slow(args.name, load(path)) else 1
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
