"""The login banner: a title, the commands people should know about, and where to find
things, drawn in the shared palette.

script() returns an update-motd.d fragment. The banner is drawn once, at deploy time,
so a login only prints text; the colours come from clustertools.render, the one palette
every command uses. The text sits in a quoted here-document: a title or description
holding $ or a backquote is printed as written, never run by the login shell.

main() reads the banner settings as JSON on stdin and writes the fragment to stdout, for
the cluster_tools role. Python 3.9 or newer, standard library only.
"""

import json
import sys

from . import render

# Banner parts -> semantic roles of the shared palette.
_ROLES = {"title": "acc", "name": "part", "link": "info", "text": "sub", "rule": "rule"}


def script(title, commands=(), links=(), flavour=render.DEFAULT_FLAVOUR, color=True):
    """The fragment for TITLE, COMMANDS and LINKS, each a list of {name, text}."""
    name = render.flavour_name(flavour)
    if not name:
        raise ValueError(f"unknown flavour {flavour!r}; choose one of {', '.join(render.FLAVOURS)}")
    t = render.Theme(name, color=color, depth=24, ascii=False)

    def paint(part, text):
        return t.paint(t.c[_ROLES[part]] + (t.bold if part == "title" else ""), text)

    rows = [*commands, *links]
    w = max((render.width(r["name"]) for r in rows), default=0)
    # The rule spans exactly the widest row: 3 spaces, the name, 2 spaces, the text,
    # less the rule's own 2-space indent.
    width = 3 + w + max((render.width(r["text"]) for r in rows), default=0)
    rule = "  " + paint("rule", t.g["rule"] * width)

    lines = ["", f"  {paint('title', t.g['sec'] + title)}", rule]
    lines += [f"   {paint('name', render.pad(c['name'], w))}  {paint('text', c['text'])}" for c in commands]
    if links:
        lines.append(rule)
        lines += [f"   {paint('link', render.pad(c['name'], w))}  {paint('text', c['text'])}" for c in links]
    lines.append("")
    # Every line is empty or indented, so none can read BANNER and end the here-document.
    body = "\n".join(lines)
    return "\n".join(
        [
            "#!/bin/sh",
            "# Printed once per login by update-motd; drawn at deploy time by roles/cluster_tools.",
            "cat <<'BANNER'",
            body,
            "BANNER",
            "",
        ]
    )


def main():
    sys.stdout.write(script(**json.load(sys.stdin)))
    return 0
