"""What a pre-push hook checks: the commits a push sends, one place for every push hook
(scripts/deepops/sensitive_guard.py, scripts/deepops/no_silent_revert.py)."""

import os
import re
import sys

ZERO_SHA = re.compile(r"^0+$")


def push_revs() -> list[list[str]] | None:
    """Revision sets for a pre-push hook; None when git said nothing at all.

    pre-commit passes on only the first ref of a push (PRE_COMMIT_FROM_REF/TO_REF), none
    for a push that reaches a root commit, and keeps the hook's stdin. So under
    pre-commit every local branch and tag is checked against what any remote already
    has (published history is out of this guard's reach either way, and a new remote
    would otherwise rescan all of it): a superset of the push, never less. Run as
    git's own hook, the refs come on stdin."""
    if os.environ.get("PRE_COMMIT_REMOTE_NAME"):
        return [["--branches", "--tags", "--not", "--remotes"]]
    lines = sys.stdin.read().splitlines()
    if not lines:
        return None
    sets = []
    for line in lines:
        _, local_sha, _, remote_sha = line.split()
        if ZERO_SHA.match(local_sha):
            continue
        sets.append(
            [local_sha, "--not", remote_sha]
            if not ZERO_SHA.match(remote_sha)
            else [local_sha, "--not", "--remotes"]
        )
    return sets
