#!/usr/bin/env python3
"""The test methods of a unittest file, as Class.method lines, so scripts/check.sh can run a
slow suite's methods side by side: the check takes as long as its slowest step, and a
file of several playbook runs was one step of up to ten minutes.

Read from the file's syntax, never by importing it. Only classes that are TestCases
(directly, or through another class of the same file) count. When a class with test
methods derives from something this cannot see (an imported helper class), nothing is
printed and the caller runs the whole file: a method is never skipped for want of a name.

  test_units.py tests/<suite>/test_x.py
"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402

TESTCASE = {"TestCase", "unittest.TestCase", "IsolatedAsyncioTestCase", "unittest.IsolatedAsyncioTestCase"}


def base_name(node):
    return ast.unparse(node)


def units(source):
    tree = ast.parse(source)
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    known = {}

    def is_case(name, seen=()):
        if name in TESTCASE:
            return True
        if name in known:
            return known[name]
        if name not in classes or name in seen:
            return None  # cannot tell
        verdicts = [is_case(base_name(b), (*seen, name)) for b in classes[name].bases]
        known[name] = True if any(v is True for v in verdicts) else (None if None in verdicts else False)
        return known[name]

    out = []
    for name, node in classes.items():
        methods = [
            f.name
            for f in node.body
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name.startswith("test")
        ]
        verdict = is_case(name)
        if verdict is None and methods:
            return []  # an unseen base: the caller runs the whole file
        if verdict:
            out += [f"{name}.{m}" for m in methods]
    return out


def main(argv):
    parser = cli.parser(__doc__)
    parser.add_argument("file")
    opts = parser.parse_args(argv[1:])
    print("\n".join(units(Path(opts.file).read_text(encoding="utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
