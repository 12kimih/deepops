#!/usr/bin/env bash
# git commit-msg hook (installed by scripts/setup.sh): the commit-msg hooks of
# .pre-commit-config.yaml on the message. pre-commit's own hook stashes every unstaged
# change in the working tree meanwhile, as at pre-commit (scripts/deepops/pre-commit.sh);
# naming the message file as the one file to check keeps it from stashing, and the
# message is all these hooks read.
set -euo pipefail
repo="$(git rev-parse --show-toplevel)"
UV_PROJECT_ENVIRONMENT="${repo}/.venv" exec uv run --project "${repo}" --locked -- \
    pre-commit run --hook-stage commit-msg --commit-msg-filename "$1" --files "$1"
