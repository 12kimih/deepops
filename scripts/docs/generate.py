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
  role-tree             the in-tree roles, as a directory tree

Usage: generate.py [--check]   (--check reports stale blocks and exits 1)
"""

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BLOCK = re.compile(
    r"(<!-- BEGIN GENERATED: (?P<name>[\w:.-]+) -->\n)(?P<body>.*?)(<!-- END GENERATED: (?P=name) -->)", re.S
)
PIN = re.compile(r"(version|_container|_image|_tag|_branch)$|_version_|_chart_version$")


def tracked(*patterns):
    """Tracked files, as paths: what a commit holds, never another checkout's half-done
    work lying in the shared tree."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *patterns], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [ROOT / name for name in out.split("\0") if name]


def literal(value):
    return isinstance(value, (str, int, float)) and not isinstance(value, bool) and "{{" not in str(value)


def defaults(role):
    path = ROOT / "roles" / role / "defaults" / "main.yml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def example_values():
    found = {}
    for path in sorted(tracked("config.example/group_vars/*.yml")):
        for name, value in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).items():
            if literal(value):
                found[name] = str(value)
    return found


def roles():
    return sorted({p.parts[-3] for p in tracked("roles/*/defaults/main.yml")} - {"galaxy"})


def cell(text):
    return str(text).replace("|", "\\|")


def gen_versions(_arg):
    example = example_values()
    rows = ["| Role | Variable | Default | config.example |", "|---|---|---|---|"]
    for role in roles():
        for name, value in defaults(role).items():
            # A pin looks like a version (1.2.3, v1.2, a commit) or an image reference.
            if (
                PIN.search(name)
                and literal(value)
                and re.match(
                    r"^(v?\d|[0-9a-f]{12,}$|[\w./-]+:[\w.-]+(@sha256:[0-9a-f]{64})?$|[\w.-]+\.tar)",
                    str(value),
                )
            ):
                # Images are pinned by digest; a 12-character prefix identifies it in the table.
                shown = re.sub(r"(@sha256:[0-9a-f]{12})[0-9a-f]{52}$", r"\1", str(value))
                rows.append(
                    f"| {role} | `{name}` | `{cell(shown)}` | {('`' + cell(example[name]) + '`') if name in example and example[name] != str(value) else ''} |"
                )
    return "\n".join(rows) + "\n"


def comments_above(path):
    """Map each top-level variable to the comment block right above it."""
    notes, pending = {}, []
    for line in path.read_text(encoding="utf-8").splitlines():
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
        shown = (
            "`"
            + cell(
                yaml.safe_dump(value, default_flow_style=True, width=1000).strip().removesuffix("...").strip()
            )
            + "`"
        )
        rows.append(f"| `{name}` | {shown} | {cell(notes.get(name, ''))} |")
    return "\n".join(rows) + "\n"


def gen_role_tree(_arg):
    # Tracked roles only, like every other check: a role another checkout has not
    # committed yet must not make this page stale for everyone.
    names = sorted(
        {p.relative_to(ROOT).parts[1] for p in tracked("roles") if len(p.relative_to(ROOT).parts) > 2}
    )
    names = [n for n in names if n != "galaxy"]
    branches = ["├──"] * (len(names) - 1) + ["└──"]
    tree = "\n".join(f"│   {b} {n}" for b, n in zip(branches, names))
    return f"```text\n├── roles\n{tree}\n```\n"


GENERATORS = {"versions": gen_versions, "role-variables": gen_role_variables, "role-tree": gen_role_tree}


def render(match):
    name = match.group("name")
    kind, _, arg = name.partition(":")
    if kind not in GENERATORS:
        raise SystemExit(f"unknown generated block: {name}")
    return match.group(1) + GENERATORS[kind](arg) + match.group(4)


def malformed(text):
    """A marker the block pattern does not take (an END misspelt or missing, CRLF,
    trailing space) would leave its block ungenerated and --check passing."""
    return text.count("<!-- BEGIN GENERATED:") != len(BLOCK.findall(text))


def main(argv):
    check = "--check" in argv
    stale = []
    for path in sorted(tracked("*.md")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "<!-- BEGIN GENERATED:" not in text:
            continue
        if malformed(text):
            print(f"malformed generated block: {path.relative_to(ROOT)}", file=sys.stderr)
            return 1
        new = BLOCK.sub(render, text)
        if new != text:
            stale.append(path.relative_to(ROOT))
            if not check:
                path.write_text(new, encoding="utf-8")
    for path in stale:
        print(f"{'stale' if check else 'updated'}: {path}")
    return 1 if (check and stale) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
