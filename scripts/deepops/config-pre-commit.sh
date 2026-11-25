#!/usr/bin/env bash
# The site config's commit hook (scripts/setup.sh links it into config/.git/hooks): the
# settings checks of scripts/deepops/config_lint.py run on what is being committed, so
# a value a deploy would refuse (a retired setting, a removal the registry does not
# know, a setting nothing reads) never reaches the clone deploys run from; and its
# pages' generated blocks (scripts/docs/generate.py) must match the settings committed.
#
# Both sides are read as committed: the site at its staged tree, deepops in a throwaway
# worktree, so work in progress in either clone blocks no one's commit. The deepops is
# the one the pages describe: DEEPOPS_REV when the caller names it (scripts/push.sh
# commits the pages a deepops change regenerates, judged by that change), else origin's
# main, fetched, which is what scripts/deepops/sync.py brings in. Never the clone's own
# HEAD: the shared clone is behind main until the owner syncs it, and judging by it
# refused every regenerated page while deepops' check, judging by main, demanded one
# (2026-11-26).
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
rev="${DEEPOPS_REV:-}"
if [ -z "${rev}" ]; then
    git -C "${deepops}" fetch -q origin main \
        || echo "config hook: could not fetch deepops; judging the pages by its last fetched main" >&2
    rev="$(git -C "${deepops}" rev-parse origin/main)"
fi
if ! git -C "${deepops}" cat-file -e "${rev}^{commit}" 2>/dev/null; then
    echo "config hook: deepops ${rev} is not in ${deepops}; fetch it there first" >&2
    exit 1
fi
export_tree "${deepops}" "${rev}" "${work}/deepops"

cd "${work}/deepops"
export DEEPOPS_CONFIG_DIR="${site}" DEEPOPS_SITE_REV="${tree}"
"${deepops}/.venv/bin/python" scripts/deepops/config_lint.py redundant dead shadow retired removals
"${deepops}/.venv/bin/python" scripts/docs/generate.py --check --site
