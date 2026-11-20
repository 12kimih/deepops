#!/bin/sh
# git reference-transaction hook (installed by scripts/setup.sh): a branch checked out
# in one worktree moves only from that worktree. Moving it from another one
# (`checkout -B`, `branch -f`, `update-ref`, a commit while it is checked out twice)
# leaves the owning worktree's index and files at the old commit, so its status shows
# every change in between reverted and its next commit makes that real. git 2.44
# refuses `checkout -B` there itself; older git (Ubuntu 24.04 ships 2.43) does not, and
# nothing in git refuses the other ways.
[ "$1" = prepared ] || exit 0

canon() { (cd "$1" 2>/dev/null && pwd -P); }

here="$(git rev-parse --show-toplevel 2>/dev/null)" || here=""
[ -n "${here}" ] && here="$(canon "${here}")"
owners="$(git worktree list --porcelain | awk '
    /^worktree / { path = substr($0, 10) }
    /^branch /   { print substr($0, 8) " " path }')"

refused=0
while read -r _old _new ref ; do
    case "${ref}" in refs/heads/*) ;; *) continue ;; esac
    owner="$(printf '%s\n' "${owners}" | awk -v r="${ref}" '$1 == r { print substr($0, length(r) + 2); exit }')"
    [ -n "${owner}" ] || continue
    owner="$(canon "${owner}")"
    [ "${owner}" = "${here}" ] && continue
    echo "branch-guard: ${ref#refs/heads/} is checked out in ${owner}; move it from there" >&2
    echo "branch-guard: (work here on a detached HEAD or another branch)" >&2
    refused=1
done
exit "${refused}"
