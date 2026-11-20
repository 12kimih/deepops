#!/usr/bin/env bash
# git pre-commit hook (installed by scripts/setup.sh): the repository checks
# (.pre-commit-config.yaml) on exactly what is being committed. pre-commit run by itself
# stashes every unstaged change in the working tree for the length of the checks
# (`git checkout -- .`): in a clone several people work in, their edits vanished
# meanwhile and could be lost on the restore. Here the staged tree goes to a throwaway
# worktree and the checks run there, on the staged files; the working tree is not
# touched, and nothing untracked in it can make a commit pass.
set -euo pipefail
here="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# shellcheck source=scripts/deepops/export-tree.sh
. "${here}/export-tree.sh"

# Under git's own GIT_INDEX_FILE (a partial commit uses a temporary index).
repo="$(git rev-parse --show-toplevel)"
tree="$(git write-tree)"
mapfile -d '' -t files < <(git diff --cached --name-only --diff-filter=d -z)
clear_git_env

work="$(mktemp -d)"
trap 'drop_tree "${repo}" "${work}/tree"; rm -rf "${work}"' EXIT
export_tree "${repo}" "${tree}" "${work}/tree"
cd "${work}/tree"
# The file list keeps pre-commit from stashing; with nothing but deletions staged,
# every file is checked.
if [ "${#files[@]}" -gt 0 ]; then args=(--files "${files[@]}"); else args=(--all-files); fi
if ! UV_PROJECT_ENVIRONMENT="${repo}/.venv" uv run --project "${repo}" --locked -- \
        pre-commit run --hook-stage pre-commit --show-diff-on-failure "${args[@]}"; then
    echo "The checks ran on a copy of what is staged: make any fix shown above in your files and stage it." >&2
    exit 1
fi
