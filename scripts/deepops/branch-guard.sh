#!/bin/sh
# git reference-transaction hook (installed by scripts/setup.sh): a branch checked out
# in one worktree moves only from that worktree. Moving it from another one
# (`checkout -B`, `branch -f`, `update-ref`, a commit while it is checked out twice)
# leaves the owning worktree's index and files at the old commit, so its status shows
# every change in between reverted and its next commit makes that real. git 2.44
# refuses `checkout -B` there itself; older git (Ubuntu 24.04 ships 2.43) does not, and
# nothing in git refuses the other ways.
#
# A worktree owns a branch while its HEAD names it, or while a rebase or bisect of it
# is under way there (HEAD is detached then). Ownership is told by git directory, so
# it holds inside a .git directory too; a bare repository's HEAD is no checkout. A
# branch two worktrees hold (`checkout -B` to the same commit gets there) moves from
# neither. Only a move to another commit is refused: pack-refs (gc) rewrites a ref with
# its own value and deletes the loose copy, git refuses deleting a checked-out branch
# itself, and a rename moves the other worktree's HEAD with it.
[ "$1" = prepared ] || exit 0

canon() { (cd "$1" 2>/dev/null && pwd -P); }
zero="$(git hash-object --stdin </dev/null | tr 0-9a-f 0)"
common="$(canon "$(git rev-parse --path-format=absolute --git-common-dir)")"
here="$(canon "$(git rev-parse --absolute-git-dir)")"
bare="$(git config --bool core.bare || true)"

# The git directories that own <ref>, one per line.
owners() {
    for dir in "${common}" "${common}"/worktrees/* ; do
        [ -f "${dir}/HEAD" ] || continue
        [ "${dir}" = "${common}" ] && [ "${bare}" = true ] && continue
        if [ "$(sed -n 's/^ref: //p' "${dir}/HEAD")" = "$1" ] \
            || [ "$(cat "${dir}/rebase-merge/head-name" 2>/dev/null)" = "$1" ] \
            || [ "$(cat "${dir}/rebase-apply/head-name" 2>/dev/null)" = "$1" ] \
            || [ "refs/heads/$(cat "${dir}/BISECT_START" 2>/dev/null)" = "$1" ] ; then
            canon "${dir}"
        fi
    done
}

# Where a git directory's files are, for the message.
worktree() {
    if [ "$1" = "${common}" ] ; then dirname "$1" ; else sed 's|/\.git$||' "$1/gitdir" ; fi
}

refused=0
while read -r _old new ref ; do
    case "${ref}" in refs/heads/*) ;; *) continue ;; esac
    [ "${new}" = "${zero}" ] && continue
    [ "${new}" = "$(git rev-parse -q --verify "${ref}")" ] && continue
    held="$(owners "${ref}")"
    [ -n "${held}" ] || continue
    [ "${held}" = "${here}" ] && continue
    # One git directory per line: a path may hold spaces.
    printf '%s\n' "${held}" | while IFS= read -r dir ; do
        echo "branch-guard: ${ref#refs/heads/} is checked out in $(worktree "${dir}")" >&2
    done
    refused=1
done
if [ "${refused}" = 1 ] ; then
    echo "branch-guard: move it from the one worktree that has it, or work here on a detached HEAD." >&2
    echo "branch-guard: git switches files before it moves the branch: after a refused checkout," >&2
    echo "branch-guard: \`git status\` here shows that; \`git checkout -f HEAD\` puts them back." >&2
fi
exit "${refused}"
