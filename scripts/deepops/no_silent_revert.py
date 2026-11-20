#!/usr/bin/env python3
"""Refuse a push that silently undoes other commits.

A commit built on an out-of-date copy of a file and committed on top of newer history
puts the file back to an older version of itself: the commits in between are undone
without anyone saying so (it happened twice in one day with several people pushing to
one branch). For each commit being pushed, and each file it changes, this looks at the
file's earlier versions on the first-parent line: content equal to one of them, other
than the parent's own, is a step back and refused. Exactly inverting a hunk of a recent
commit while the file has other edits too is reported as a warning (on its own it is as
often a deliberate reversal). In a refused commit, deleting a file a recent
commit added is listed too (a stale tree lacks the newer files); a deletion alone is
the ordinary end of a refactor and passes. A commit whose message says it reverts, reapplies or restores is meant to, and passes; the
commits it would undo are printed so the author can tell.

  no_silent_revert.py push            as a pre-push hook (the pushed commits)
  no_silent_revert.py range REV...    any rev-list arguments, e.g. origin/main..HEAD
"""

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from push_range import push_revs  # noqa: E402

# git revert writes "Revert ..." and, reverting a revert, "Reapply ...".
ESCAPE = re.compile(r"\b(revert|reapply|restor)", re.IGNORECASE)
# How far back a deleted file counts as recently added, and a hunk as recent.
RECENT = 30
# A hunk this many changed lines or more is too specific to be inverted by chance.
HUNK_LINES = 3


def git(*args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=check).stdout


def blob(rev: str, path: str) -> str | None:
    out = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", f"{rev}:{path}"], capture_output=True, text=True
    )
    return out.stdout.strip() or None


def subject(rev: str) -> str:
    return f"{rev[:12]} {git('log', '-1', '--format=%s', rev).strip()}"


def hunks(old: str, new: str, path: str) -> list[tuple[Counter, Counter]]:
    """Each hunk of `git diff old new -- path` as (removed lines, added lines); none when
    either side is missing (a root commit has no parent to compare with)."""
    out, removed, added = [], Counter(), Counter()
    diff = subprocess.run(
        ["git", "diff", "-U0", "--no-color", old, new, "--", path], capture_output=True, text=True
    )
    if diff.returncode != 0:
        return []
    for line in diff.stdout.splitlines():
        if line.startswith("@@"):
            if removed or added:
                out.append((removed, added))
            removed, added = Counter(), Counter()
        elif line.startswith("-") and not line.startswith("---"):
            removed[line[1:]] += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added[line[1:]] += 1
    if removed or added:
        out.append((removed, added))
    return out


def inverted_hunk(parent: str, commit: str, path: str, revs: list[str]) -> str | None:
    """The first recent commit one of whose hunks `commit` exactly inverts in `path`: the
    hunks of `commit` that remove what it added remove all of it and add back only what
    it removed (nothing new in its place, unlike a rewrite). A stale copy with the
    author's own edits elsewhere in the file does that while the file as a whole is new.
    An own edit inside the same hunk looks like a rewrite and is not caught."""
    mine = hunks(parent, commit, path)
    for rev in revs:
        for their_removed, their_added in hunks(f"{rev}^", rev, path):
            if sum(their_removed.values()) + sum(their_added.values()) < HUNK_LINES:
                continue
            touched = [(r, a) for r, a in mine if (r & their_added) or (a & their_removed)]
            removed = sum((r for r, _ in touched), Counter())
            added = sum((a for _, a in touched), Counter())
            if (
                touched
                and not (their_added - removed)
                and not (added - their_removed)
                and not (their_removed - added)
            ):
                return rev
    return None


def findings_for(commit: str) -> tuple[list[str], list[str]]:
    """(refusals, warnings) for one commit. A whole file back to an older version, or a
    recently deleted one back as it was, is a refusal, with the newer files the same
    commit deletes; an exactly inverted hunk
    alone is a warning (in history it marked deliberate reversals as often as not)."""
    parents = git("rev-list", "--parents", "-n", "1", commit).split()[1:]
    if len(parents) != 1 or ESCAPE.search(git("log", "-1", "--format=%B", commit)):
        return [], []  # a root, a merge, or a declared revert
    parent, back, deleted, inverted = parents[0], [], [], []
    changes = git("diff-tree", "-r", "--no-commit-id", "--no-renames", "--name-status", parent, commit)
    recent = set(git("rev-list", "--first-parent", "-n", str(RECENT), parent).split())
    for line in changes.splitlines():
        status, _, path = line.partition("\t")
        if status == "M":
            new = blob(commit, path)
            touching = git("log", "--first-parent", "--format=%H", parent, "--", path).split()
            for k, rev in enumerate(touching):
                if blob(f"{rev}^", path) == new:
                    undone = ", ".join(subject(r) for r in touching[: k + 1])
                    back.append(f"{path} goes back to its content before {undone}")
                    break
            else:
                rev = inverted_hunk(parent, commit, path, [r for r in touching if r in recent])
                if rev:
                    inverted.append(f"{path} undoes a change of {subject(rev)}")
        elif status == "A":
            # A recently deleted file back exactly as it was: a stale tree resurrects it.
            gone = git(
                "log", "--first-parent", "--diff-filter=D", "-n", "1", "--format=%H", parent, "--", path
            )
            gone = gone.strip()
            if gone in recent and blob(f"{gone}^", path) == blob(commit, path):
                back.append(f"{path} comes back as it was before {subject(gone)} deleted it")
        elif status == "D":
            added = git("log", "--first-parent", "--diff-filter=A", "--format=%H", parent, "--", path).split()
            if added and added[0] in recent:
                deleted.append(f"{path} is deleted, added by {subject(added[0])}")
    head = subject(commit)
    if back:  # a stale tree: everything it undid is part of the refusal
        return [f"{head}: {f}" for f in back + inverted + deleted], []
    return [], [f"{head}: {f}" for f in inverted]


def check(revs: list[str]) -> tuple[list[str], list[str]]:
    refused, warned = [], []
    for commit in git("rev-list", "--reverse", "--no-merges", *revs).split():
        r, w = findings_for(commit)
        refused += r
        warned += w
    return refused, warned


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("push", "range"):
        print(__doc__, file=sys.stderr)
        return 2
    if argv[0] == "range":
        sets = [argv[1:]]
    else:
        sets = push_revs()
        if sets is None:
            return 0
    refused, warned = [], []
    for revs in sets:
        r, w = check(revs)
        refused += r
        warned += w
    if warned:
        print("Check that these reversals are meant (say revert/restore in the message to silence this):")
        print("\n".join(f"  {f}" for f in warned))
    if refused:
        print("A commit undoes newer commits without saying so (an out-of-date copy committed on top?):")
        print("\n".join(f"  {f}" for f in refused))
        print(
            "Rebase onto the remote and redo the change, or say revert/restore in the message if it is meant."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
