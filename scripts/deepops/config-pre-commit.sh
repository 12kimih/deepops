#!/usr/bin/env bash
# The site config's commit hook (scripts/setup.sh links it into config/.git/hooks): the
# settings checks of scripts/deepops/config_lint.py run on what is being committed, so
# a value a deploy would refuse (a retired setting, a removal the registry does not
# know, a setting nothing reads) never reaches the clone deploys run from.
#
# Both sides are read as committed: the site at its staged tree, deepops at its HEAD in
# a throwaway worktree, so work in progress in either clone blocks no one's commit.
set -euo pipefail

tree="$(git write-tree)"
site="$(git rev-parse --show-toplevel)"
deepops="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)"
# shellcheck source=scripts/deepops/export-tree.sh
. "${deepops}/scripts/deepops/export-tree.sh"
# The hook runs with this repository's GIT_DIR and index; the checks read deepops too.
clear_git_env

work="$(mktemp -d)"
trap 'drop_tree "${deepops}" "${work}/deepops"; rm -rf "${work}"' EXIT
export_tree "${deepops}" HEAD "${work}/deepops"

cd "${work}/deepops"
DEEPOPS_CONFIG_DIR="${site}" DEEPOPS_SITE_REV="${tree}" \
    "${deepops}/.venv/bin/python" scripts/deepops/config_lint.py redundant dead shadow retired removals
