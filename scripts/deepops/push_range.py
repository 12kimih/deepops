"""What a pre-push hook checks: the commits a push sends, one place for every push hook
(scripts/deepops/sensitive_guard.py, scripts/deepops/no_silent_revert.py)."""

import re
import sys

ZERO_SHA = re.compile(r"^0+$")


def push_revs() -> list[list[str]] | None:
    """Revision sets for git's pre-push hook (scripts/deepops/pre-push.sh): one per ref
    on stdin, what it sends that its remote ref (or, for a new ref, any remote) lacks;
    None when git said nothing at all."""
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
