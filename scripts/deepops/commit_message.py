#!/usr/bin/env python3
"""Commit messages in these repositories (deepops and the site config) are plain ASCII
and carry no tool-attribution trailers.

A message is read by every tool that shows history (terminals, mail, logs in other
encodings) and stays in history for good: rewriting it later means rewriting every
commit after it. Two messages with non-ASCII text were pushed (2026-11-22) because the
rule was written down but nothing checked it. This checks it where a message is made
and again where it leaves the clone, so a commit made without hooks is caught too.

  commit_message.py message FILE   a commit-msg hook (deepops: .pre-commit-config.yaml)
  commit_message.py push           a pre-push hook (the pushed commits, refs on stdin)
  commit_message.py range REV...   any rev-list arguments, e.g. origin/main..HEAD

Linked as a git hook itself (the site config's hooks, scripts/setup.sh), it takes its
mode from the hook's name: commit-msg or pre-push.
"""

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402
from push_range import push_revs  # noqa: E402

# Trailers a tool adds to say it wrote the commit; the author is the person committing.
ATTRIBUTION = re.compile(
    r"^(co-authored-by:.*\b(claude|anthropic|copilot|openai|chatgpt|codex)\b"
    r"|.*generated (with|by) .*\b(claude|copilot|chatgpt|codex)\b)",
    re.IGNORECASE,
)
# git's scissors line: what follows it is not part of the message (commit -v).
SCISSORS = re.compile(r"^# -+ >8 -+$")
HOOK_MODES = {"commit-msg": "message", "pre-push": "push"}


def problems(message: str) -> list[str]:
    """What is wrong with one message; comment lines and the scissors part are not in it."""
    found = []
    for number, line in enumerate(message.splitlines(), 1):
        if SCISSORS.match(line):
            break
        if line.startswith("#"):
            continue
        odd = sorted({c for c in line if ord(c) > 127})
        if odd:
            found.append(f"line {number}: non-ASCII {''.join(odd)!r}: {line.strip()[:80]}")
        if ATTRIBUTION.match(line.strip()):
            found.append(f"line {number}: a tool-attribution trailer: {line.strip()[:80]}")
    return found


def commits(revs: list[str]) -> list[tuple[str, str]]:
    out = subprocess.run(
        ["git", "log", "--format=%H%x00%B%x01", *revs],
        capture_output=True,
        text=True,
        errors="replace",
        check=True,
    ).stdout
    found = []
    for entry in filter(str.strip, out.split("\x01")):
        sha, _, body = entry.strip("\n").partition("\x00")
        found.append((sha, body))
    return found


def report(where: str, found: list[str]) -> None:
    for item in found:
        print(f"commit-message: {where}: {item}", file=sys.stderr)


def main(argv: list[str]) -> int:
    hook = HOOK_MODES.get(Path(argv[0]).name)
    parser = cli.parser(__doc__)
    parser.add_argument("mode", choices=["message", "push", "range"])
    parser.add_argument("args", nargs="*")
    opts = parser.parse_args([hook, *argv[1:]] if hook else argv[1:])

    bad = 0
    if opts.mode == "message":
        if len(opts.args) != 1:
            parser.error("message takes the message file")
        found = problems(Path(opts.args[0]).read_text(encoding="utf-8", errors="replace"))
        report("this commit", found)
        bad = len(found)
    else:
        if opts.mode == "push":
            sets = push_revs()
            if sets is None:
                return 0
        else:
            if not opts.args:
                parser.error("range takes revisions, e.g. origin/main..HEAD")
            sets = [opts.args]
        for revs in sets:
            for sha, body in commits(revs):
                found = problems(body)
                report(sha[:12], found)
                bad += len(found)
    if bad:
        print(
            "commit-message: write the message in plain ASCII without tool-attribution "
            "trailers (git commit --amend for the last commit; rebase for earlier ones).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
