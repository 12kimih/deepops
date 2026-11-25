#!/usr/bin/env bash
# Check and push this checkout's commits to origin's main; what is pushed is what was checked.
#   scripts/push.sh            rebase onto origin/main, run scripts/check.sh, push
#   (DEEPOPS_CONFIG_DIR: the site config checkout, checked at its origin's main; by
#   default the checkout's config/, else the clone's; none: check without a site config)
#
# Several checkouts of one clone (git worktrees) push to one main. Each rebases onto
# origin/main and runs the full check without a lock, so checkouts check at the same
# time; then it takes a lock in the clone's shared git directory and pushes at once when
# neither origin (deepops, nor the site config's) moved since its check. Only when one
# did does it rebase and check again, holding the lock, so what is pushed is always
# what was checked. The lock is held for seconds unless a re-check is needed; the next
# checkout waits (it says for whom). The lock goes with the process however it ends.
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
# Fetches of several checkouts of one clone at once race for its refs: one at a time.
fetch() {
    (flock 8; git -C "$1" fetch -q "${remote}" "${branch}") 8>>"$(git -C "$1" rev-parse --path-format=absolute --git-common-dir)/deepops-fetch.lock"
}

# The site config the check reads: never the checkout itself, which a deploy may be
# running from (scripts/deepops/sync.py moves the shared ones only between runs). Its
# origin's main goes into a throwaway worktree with the checkout's own unpushed commits
# rebased on top (a private clone's half of a change pair), so a config change a peer
# landed with its deepops half is checked too; uncommitted edits are not read, as the
# config's own commit hook reads committed trees. A config that is no git checkout is
# read as it is. A worktree has no config/ of its own: it checks with the clone's, and
# with none at all the push is refused unless DEEPOPS_CONFIG_DIR=none says so (a check
# without the site's pages passed a change that left them stale, 2026-11-26).
#
# The site's pages carry blocks generated from deepops (scripts/docs/generate.py): a
# deepops change can make them stale. Their regenerated text is committed in the
# throwaway tree (its hook judging them by this very deepops, DEEPOPS_REV) and pushed
# to the config's origin right after deepops, so the pair lands together and no one's
# next check finds the config behind.
clone_config="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/config"
if [[ "${DEEPOPS_CONFIG_DIR:-}" == none ]]; then
    config=""
elif [[ -n "${DEEPOPS_CONFIG_DIR:-}" ]]; then
    config="$(readlink -f "${DEEPOPS_CONFIG_DIR}")"
elif [[ -e config ]]; then
    config="$(readlink -f config)"
elif [[ -e "${clone_config}" ]]; then
    config="$(readlink -f "${clone_config}")"
else
    echo "push.sh: no site config to check with (no config/ here or in the clone); set DEEPOPS_CONFIG_DIR, or DEEPOPS_CONFIG_DIR=none to check without one" >&2
    exit 2
fi
if [[ -n "${config}" && ! -e "${config}" ]]; then
    echo "push.sh: DEEPOPS_CONFIG_DIR ${config} does not exist" >&2
    exit 2
fi
python="${DEEPOPS_PYTHON:-.venv/bin/python}"
[[ -x "${python}" ]] || python=python3
config_pages=""
config_tree=""
config_base=""
drop_config_tree() {
    if [[ -n "${config_tree}" ]]; then
        git -C "${config}" worktree remove --force "${config_tree}" >/dev/null 2>&1 || true
        rm -rf "$(dirname "${config_tree}")"
        config_tree=""
    fi
}
trap drop_config_tree EXIT
make_config_tree() {
    drop_config_tree
    config_pages=""
    if [[ -z "${config}" ]]; then
        config_tree_dir=none
        return 0
    fi
    if ! git -C "${config}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        config_tree_dir="${config}"
        return 0
    fi
    fetch "${config}"
    config_base="$(git -C "${config}" rev-parse "${remote}/${branch}")"
    config_tree="$(mktemp -d)/config"
    git -C "${config}" worktree add -q --detach "${config_tree}" HEAD
    if ! git -C "${config_tree}" rebase -q "${config_base}" >/dev/null 2>&1; then
        git -C "${config_tree}" rebase --abort >/dev/null 2>&1 || true
        echo "push.sh: the config checkout's unpushed commits do not rebase onto ${remote}/${branch}; rebase them there first" >&2
        return 1
    fi
    config_tree_dir="${config_tree}"
    regenerate_pages
}

# The site's pages regenerated from this deepops, committed in the throwaway tree when
# they changed: config_pages is then that commit, pushed after deepops. The checkout's
# own unpushed commits go with it, so they must already sit on the config's origin
# (pushed as they are, the checkout stays level with its origin).
regenerate_pages() {
    [[ -f scripts/docs/generate.py ]] || return 0
    DEEPOPS_CONFIG_DIR="${config_tree}" "${python}" scripts/docs/generate.py --site >/dev/null
    [[ -n "$(git -C "${config_tree}" status --porcelain)" ]] || return 0
    if ! git -C "${config}" merge-base --is-ancestor "${config_base}" HEAD; then
        echo "push.sh: this change regenerates the site config's pages, and the config checkout has unpushed commits behind ${remote}/${branch}; rebase or push them first" >&2
        return 1
    fi
    git -C "${config_tree}" add -A
    DEEPOPS_REV="$(git rev-parse HEAD)" git -C "${config_tree}" commit -q \
        -m "docs: regenerate the pages from deepops $(git rev-parse --short=12 HEAD)"
    config_pages="$(git -C "${config_tree}" rev-parse HEAD)"
    echo "push.sh: the site config's pages change with this push: $(git -C "${config_tree}" diff --name-only HEAD~1 | tr '\n' ' ')" >&2
}

config_origin() {  # the config's origin main now ("" for a config that is no checkout)
    if [[ -n "${config}" ]] && git -C "${config}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        fetch "${config}"
        git -C "${config}" rev-parse "${remote}/${branch}"
    fi
}

# Rebase onto origin/main and run the full check there, with the config tree; base and
# config_base are what was checked. Exits when origin already holds HEAD.
check_on_origin() {
    fetch .
    base="$(git rev-parse "${remote}/${branch}")"
    if git merge-base --is-ancestor HEAD "${base}"; then
        echo "push.sh: nothing to push; ${remote}/${branch} already holds HEAD"
        exit 0
    fi
    git rebase -q "${base}"
    make_config_tree || exit 2
    DEEPOPS_CONFIG_DIR="${config_tree_dir}" "${check[@]}"
}

check_on_origin
exec 9>>"${lock}"
if ! flock -n 9; then
    echo "push.sh: checked; waiting to push for $(cat "${lock}" 2>/dev/null || echo 'another checkout')" >&2
    flock 9
fi
printf 'pid %s, %s, %s, since %s\n' "$$" "$(id -un)" "${PWD}" "$(date -Is)" > "${lock}"
while :; do
    fetch .
    if [[ "$(git rev-parse "${remote}/${branch}")" == "${base}" && "$(config_origin)" == "${config_base}" ]]; then
        git push -q "${remote}" "HEAD:${branch}"
        echo "push.sh: pushed $(git log --oneline -1 HEAD)"
        if [[ -n "${config_pages}" ]]; then
            if ! git -C "${config_tree}" push -q "${remote}" "HEAD:${branch}"; then
                echo "push.sh: deepops is pushed but the site config's pages are not; push them: git -C ${config} push ${remote} ${config_pages}:${branch}" >&2
                exit 1
            fi
            echo "push.sh: pushed the site config's pages $(git -C "${config_tree}" log --oneline -1 HEAD)"
        fi
        exit 0
    fi
    echo "push.sh: origin moved since the check; rebasing and checking again" >&2
    check_on_origin
done
