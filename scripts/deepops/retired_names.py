#!/usr/bin/env python3
"""retired_names: where a retired command is still named, so a page, a notice, a help
line or a test never sends people to a command that is gone (owner, 2026-11-27).

    mentions(text, retired)  -> [(line number, the name, its replacement)]
    RETIRED                  {old: new}, cluster_tools_retired_builtin (roles/cluster_tools_defaults)

"The command" is the name as a word: not after "#" (a Slack channel) or "/" (a path),
not before "." (a file) or "-" (a longer name, scratch-space-purge), never inside a
longer word. One rule for every repository: deepops' own guard
(tests/cluster-tools/test_retired_names.py), the user docs' and the site config's checks
(site.json commands_retired, scripts/docs/site_facts.py) all judge by it.
Python 3.9 or newer; PyYAML.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yaml_load  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RETIRED = yaml_load.load((ROOT / "roles/cluster_tools_defaults/defaults/main.yml").read_text())[
    "cluster_tools_retired_builtin"
]


def pattern(retired=None):
    """The regex of a retired name as a word (the module docstring's rule)."""
    names = sorted(retired if retired is not None else RETIRED, key=len, reverse=True)
    return re.compile(r"(?<![#\w/.-])(" + "|".join(map(re.escape, names)) + r")(?![\w.-])")


def mentions(text, retired=None):
    """[(line number, name, replacement)] of TEXT's mentions of a retired command."""
    retired = RETIRED if retired is None else retired
    found, word = [], pattern(retired)
    for number, line in enumerate(text.splitlines(), 1):
        found += [(number, m.group(1), retired[m.group(1)]) for m in word.finditer(line)]
    return found
