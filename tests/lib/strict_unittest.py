"""python -m unittest that also fails on a ResourceWarning: the check runs every suite
through it (scripts/check.sh).

unittest prints a ResourceWarning (a file, socket or child process a test or the code
under test left open) but passes, and the check shows a passing suite's output to no
one: 24 of them sat in the validation tests (2026-11). This runs unittest as
`python -m unittest` would, with the same arguments, and exits non-zero when any was
raised, naming each.
"""

import gc
import os
import sys
import unittest
import warnings

seen = []
_show = warnings.showwarning


def _record(message, category, filename, lineno, file=None, line=None):
    if issubclass(category, ResourceWarning):
        seen.append(f"{filename}:{lineno}: {message}")
    _show(message, category, filename, lineno, file, line)


warnings.showwarning = _record
sys.path[0] = os.getcwd()  # as `python -m unittest` has it, not this file's directory
program = unittest.main(module=None, argv=["python -m unittest", *sys.argv[1:]], exit=False)
gc.collect()  # what nothing references any longer warns now, not after the check
if seen:
    print(f"\n{len(seen)} ResourceWarning(s): close what is opened (with, or .close()):", file=sys.stderr)
    for where in seen:
        print(f"  {where}", file=sys.stderr)
    sys.exit(1)
sys.exit(0 if program.result.wasSuccessful() else 1)
