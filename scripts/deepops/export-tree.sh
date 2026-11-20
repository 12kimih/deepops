#!/usr/bin/env bash
# Sourced by the commit hooks (pre-commit.sh, config-pre-commit.sh): a tree exactly as it
# is being committed, in a throwaway worktree, so the checks read neither a peer's
# unstaged edits nor untracked files in a shared clone, and change nothing there.
#
#   export_tree <repository> <tree-ish> <directory>
#   drop_tree   <repository> <directory>
#
# The worktree's index holds <tree-ish> (HEAD stays the repository's), so `git diff
# --cached` there is what is being committed. What the checks read but no commit holds
# is linked from <repository>: the Kubespray checkout, the Galaxy content, the site
# config.
export_tree() {
    local repo="$1" tree="$2" dir="$3" path
    git -C "${repo}" worktree add --quiet --detach --no-checkout "${dir}" HEAD
    git -C "${dir}" read-tree "${tree}"
    git -C "${dir}" checkout-index --all --force
    for path in submodules/kubespray roles/galaxy collections config; do
        if [ -e "${repo}/${path}" ]; then
            rmdir "${dir}/${path}" 2>/dev/null || true  # an empty submodule directory
            [ -e "${dir}/${path}" ] || ln -s "${repo}/${path}" "${dir}/${path}"
        fi
    done
}

drop_tree() {
    git -C "$1" worktree remove --force "$2" >/dev/null 2>&1 || true
}

# A hook runs with git's GIT_* variables set for the repository it serves; commands on
# another repository or worktree must not inherit them.
clear_git_env() {
    local name
    while IFS='=' read -r name _; do
        unset "${name}"
    done < <(env | grep -E '^GIT_[A-Z_]+=' || true)
}
