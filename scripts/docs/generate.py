#!/usr/bin/env python3
"""Fill the generated blocks in the docs from the files they describe.

Docs that restate a version or a default go stale the next time the role
changes. Instead, a doc marks a block

    <!-- BEGIN GENERATED: versions -->
    <!-- END GENERATED: versions -->

and this script writes its body from the source (role defaults, the example
config). Hand edits inside a block are overwritten.

Generators:
  versions              every pinned version, image and chart in role defaults
  role-variables:ROLE   the variables of roles/ROLE/defaults/main.yml, with the
                        comment above each one as its description

Usage: generate.py [--check]   (--check reports stale blocks and exits 1)
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BLOCK = re.compile(r"(<!-- BEGIN GENERATED: (?P<name>[\w:.-]+) -->\n)(?P<body>.*?)(<!-- END GENERATED: (?P=name) -->)", re.S)
PIN = re.compile(r"(version|_container|_image|_tag|_branch)$|_version_|_chart_version$")


def literal(value):
    return isinstance(value, (str, int, float)) and not isinstance(value, bool) and "{{" not in str(value)


def defaults(role):
    path = ROOT / "roles" / role / "defaults" / "main.yml"
    return (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}


def example_values():
    found = {}
    for path in sorted((ROOT / "config.example" / "group_vars").glob("*.yml")):
        for name, value in (yaml.safe_load(path.read_text()) or {}).items():
            if literal(value):
                found[name] = str(value)
    return found


def roles():
    return sorted(p.name for p in (ROOT / "roles").iterdir() if (p / "defaults" / "main.yml").exists() and p.name != "galaxy")


def cell(text):
    return str(text).replace("|", "\\|")


def gen_versions(_arg):
    example = example_values()
    rows = ["| Role | Variable | Default | config.example |", "|---|---|---|---|"]
    for role in roles():
        for name, value in defaults(role).items():
            # A pin looks like a version (1.2.3, v1.2, a commit) or an image reference.
            if PIN.search(name) and literal(value) and re.match(r"^(v?\d|[0-9a-f]{12,}$|[\w./-]+:[\w.-]+$|[\w.-]+\.tar)", str(value)):
                rows.append(f"| {role} | `{name}` | `{cell(value)}` | {('`' + cell(example[name]) + '`') if name in example and example[name] != str(value) else ''} |")
    return "\n".join(rows) + "\n"


def comments_above(path):
    """Map each top-level variable to the comment block right above it."""
    notes, pending = {}, []
    for line in path.read_text().splitlines():
        if not line.strip():
            pending = []
            continue
        if line.startswith("#"):
            pending.append(line.lstrip("#").strip())
            continue
        m = re.match(r"^([a-z_][a-z0-9_]*):", line)
        if m:
            notes[m.group(1)] = " ".join(p for p in pending if p)
        pending = []
    return notes


def gen_role_variables(role):
    path = ROOT / "roles" / role / "defaults" / "main.yml"
    notes = comments_above(path)
    rows = ["| Variable | Default | Description |", "|---|---|---|"]
    for name, value in defaults(role).items():
        shown = "`" + cell(yaml.safe_dump(value, default_flow_style=True, width=1000).strip().removesuffix("...").strip()) + "`"
        rows.append(f"| `{name}` | {shown} | {cell(notes.get(name, ''))} |")
    return "\n".join(rows) + "\n"


GENERATORS = {"versions": gen_versions, "role-variables": gen_role_variables}


def render(match):
    name = match.group("name")
    kind, _, arg = name.partition(":")
    if kind not in GENERATORS:
        raise SystemExit(f"unknown generated block: {name}")
    return match.group(1) + GENERATORS[kind](arg) + match.group(4)


def main(argv):
    check = "--check" in argv
    stale = []
    for path in sorted(ROOT.glob("**/*.md")):
        if any(part in ("submodules", "galaxy", "node_modules", ".git") for part in path.parts):
            continue
        text = path.read_text()
        if "<!-- BEGIN GENERATED:" not in text:
            continue
        new = BLOCK.sub(render, text)
        if new != text:
            stale.append(path.relative_to(ROOT))
            if not check:
                path.write_text(new)
    for path in stale:
        print(f"{'stale' if check else 'updated'}: {path}")
    return 1 if (check and stale) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
