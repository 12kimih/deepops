"""What a pre-push hook checks: the commits a push sends, one place for every push hook
(scripts/deepops/sensitive_guard.py, no_silent_revert.py, commit_message.py)."""

import os
import re
import sys

ZERO_SHA = re.compile(r"^0+$")


def push_revs(stdin=None, environ=None) -> list[list[str]]:
    """Revision sets for a pre-push hook, one per ref the push sends: what it sends that
    its remote ref (or, for a new ref, any remote) lacks.

    git's own pre-push hook (scripts/deepops/pre-push.sh) gives every ref on stdin. Under
    pre-commit (another repository using these hooks from .pre-commit-hooks.yaml),
    pre-commit reads stdin itself and hands a hook the first ref's range as
    PRE_COMMIT_FROM_REF..PRE_COMMIT_TO_REF (no range when it pushes a whole history: then
    all that no remote has). With none of these the hook would check nothing and pass, so
    it stops instead."""
    stdin = sys.stdin if stdin is None else stdin
    environ = os.environ if environ is None else environ
    lines = stdin.read().splitlines()
    if lines:
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
    to_ref, from_ref = environ.get("PRE_COMMIT_TO_REF"), environ.get("PRE_COMMIT_FROM_REF")
    if to_ref and from_ref:
        return [[to_ref, "--not", from_ref]]
    if to_ref or environ.get("PRE_COMMIT_REMOTE_NAME"):
        # A first push of a whole history: pre-commit gives no range, only the remote.
        return [[to_ref or "HEAD", "--not", "--remotes"]]
    print(
        "push: no refs to check were given (run as git's pre-push hook, which passes them on "
        "stdin, or under pre-commit's pre-push stage)",
        file=sys.stderr,
    )
    raise SystemExit(2)
