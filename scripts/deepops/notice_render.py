#!/usr/bin/env python3
"""Check a notices.json the way the cluster bot will post it: each notice rendered by
cluster_tools.rich (the one renderer of Slack text, through cluster_bot.notices.render)
and nothing left that Slack would show as typed or could not take (a marker this
Markdown does not read, a title over Slack's limit). The user docs run it on every
commit, so a notice the bot cannot show fails where it is written, not in the channel.

    notice_render.py FILE            a notices.json
    notice_render.py -- COMMAND...   the notices.json COMMAND prints (the docs' export)

A pre-commit hook (notice-render, .pre-commit-hooks.yaml) for other repositories. Exit
0 when every notice renders as written, 1 with what does not, 2 on a usage error.
Python 3.9 or newer, standard library only.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(Path(__file__).resolve().parent),
    str(ROOT / "roles/cluster_bot/files"),
    str(ROOT / "roles/cluster_tools_lib/files"),
]
import cli  # noqa: E402
from cluster_bot import notices  # noqa: E402


def load(path, command):
    if command:
        done = subprocess.run(command, capture_output=True, text=True, errors="replace", check=False)
        if done.returncode:
            raise ValueError(f"{' '.join(command)} failed ({done.returncode}): {done.stderr.strip()}")
        return json.loads(done.stdout)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv):
    own, command = (argv[: argv.index("--")], argv[argv.index("--") + 1 :]) if "--" in argv else (argv, [])
    parser = cli.parser(__doc__)
    parser.usage = "%(prog)s FILE | %(prog)s -- COMMAND..."
    parser.add_argument("file", nargs="?", help="a notices.json")
    args = parser.parse_args(own)
    if bool(args.file) == bool(command):
        parser.error("give a FILE or -- COMMAND..., one of them")
    try:
        document = load(args.file, command)
    except (OSError, ValueError) as e:  # json's errors are ValueErrors
        print(f"notice-render: {e}", file=sys.stderr)
        return 2
    found = notices.check(document)
    for line in found:
        print(f"notice-render: {line}", file=sys.stderr)
    if found:
        print("notice-render: write the notice in the Markdown cluster_tools.rich reads "
              "(**bold**, `code`, [text](url), - lists)", file=sys.stderr)  # fmt: skip
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
