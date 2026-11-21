#!/usr/bin/env python3
r"""Parse every role template with Jinja and fail on syntax errors.

The template module renders these files through Jinja before installing them,
so a file that Jinja cannot parse breaks the play at deploy time even when the
file is perfectly valid in its own language. Shell scripts are the usual
victim: bash's array-length expansion contains a brace followed by a hash,
which opens a Jinja comment and aborts the render with "Missing end of comment
tag".

Only syntax is checked, with Jinja's parser alone: templates are never compiled
or rendered, so neither variables nor Ansible's filters and tests are needed and
every template is checked. Ansible enables no Jinja extensions by default, so
plain Jinja accepts exactly what a deploy does. A string literal with an escape
Python does not know (a regex's `\s` written with one backslash) fails too: Jinja
unescapes literals in templates, and Python deprecates such escapes and will refuse
them (write `\\s`).

    check-template-syntax.py    every tracked roles/*/templates/** file
"""

import sys
import warnings
from pathlib import Path

from jinja2 import Environment
from jinja2.exceptions import TemplateSyntaxError

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402
from repo_files import ROOT, tracked  # noqa: E402


def problem(text):
    """Why Jinja refuses TEXT as a template ("LINE: message"), or None."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            Environment().parse(text)
    except TemplateSyntaxError as err:
        return f"{err.lineno}: {err.message}"
    except DeprecationWarning as err:
        return f"?: {err}"
    return None


def main(argv):
    cli.parser(__doc__).parse_args(argv)
    templates = tracked("roles/*/templates/*")
    failures = []
    for path in templates:
        found = problem(path.read_text(encoding="utf-8"))
        if found:
            failures.append(f"{path.relative_to(ROOT)}:{found}")
    for failure in failures:
        print(failure, file=sys.stderr)
    print(f"{len(templates)} templates parsed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
