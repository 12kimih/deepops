#!/usr/bin/env python3
"""Parse every role template with Jinja and fail on syntax errors.

The template module renders these files through Jinja before installing them,
so a file that Jinja cannot parse breaks the play at deploy time even when the
file is perfectly valid in its own language. Shell scripts are the usual
victim: bash's array-length expansion contains a brace followed by a hash,
which opens a Jinja comment and aborts the render with "Missing end of comment
tag".

Only syntax is checked, with Jinja's parser alone: templates are never compiled
or rendered, so neither variables nor Ansible's filters and tests are needed and
every template is checked. Ansible enables no Jinja extensions by default, so
plain Jinja accepts exactly what a deploy does.

    check-template-syntax.py    every tracked roles/*/templates/** file
"""

import sys
from pathlib import Path

from jinja2 import Environment
from jinja2.exceptions import TemplateSyntaxError

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402
from repo_files import ROOT, tracked  # noqa: E402


def main(argv):
    cli.parser(__doc__).parse_args(argv)
    env = Environment()
    templates = tracked("roles/*/templates/*")
    failures = []
    for path in templates:
        try:
            env.parse(path.read_text(encoding="utf-8"))
        except TemplateSyntaxError as err:
            failures.append(f"{path.relative_to(ROOT)}:{err.lineno}: {err.message}")
    for failure in failures:
        print(failure, file=sys.stderr)
    print(f"{len(templates)} templates parsed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
