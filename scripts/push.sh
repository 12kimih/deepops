#!/usr/bin/env bash
# Check and push this checkout's commits to origin's main, one checkout at a time.
#   scripts/push.sh            rebase onto origin/main, run scripts/check.sh, push
#
# Several checkouts of one clone (git worktrees) that each ran the ten-minute full check
# and then pushed kept losing the race: origin moved during the check, the push was
# refused, the check ran again. This holds a lock in the clone's shared git directory
# from the rebase to the push, so the next checkout waits (it says for whom) and then
# checks on top of what was pushed. The lock goes with the process however it ends.
# A push made without this script still races it; then the rebase and check run again.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
remote=origin
branch=main
check=(scripts/check.sh)

if [[ -n "$(git status --porcelain)" ]]; then
    echo "push.sh: the checkout has uncommitted changes; commit or remove them first" >&2
    exit 2
fi

lock="$(git rev-parse --git-common-dir)/deepops-push.lock"
exec 9>>"${lock}"
if ! flock -n 9; then
    echo "push.sh: waiting for $(cat "${lock}" 2>/dev/null || echo 'another checkout')" >&2
    flock 9
fi
printf 'pid %s, %s, %s, since %s\n' "$$" "$(id -un)" "${PWD}" "$(date -Is)" > "${lock}"

while :; do
    git fetch -q "${remote}" "${branch}"
    base="$(git rev-parse "${remote}/${branch}")"
    if git merge-base --is-ancestor HEAD "${base}"; then
        echo "push.sh: nothing to push; ${remote}/${branch} already holds HEAD"
        exit 0
    fi
    git rebase -q "${base}"
    "${check[@]}"
    git fetch -q "${remote}" "${branch}"
    if [[ "$(git rev-parse "${remote}/${branch}")" == "${base}" ]]; then
        git push -q "${remote}" "HEAD:${branch}"
        echo "push.sh: pushed $(git log --oneline -1 HEAD)"
        exit 0
    fi
    echo "push.sh: ${remote}/${branch} moved during the check (a push without push.sh); again" >&2
done
