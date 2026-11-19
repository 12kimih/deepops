#!/usr/bin/env bash
# The site config's commit hook (scripts/setup.sh links it into config/.git/hooks): the
# settings checks of scripts/deepops/config_lint.py run on what is being committed, so
# a value a deploy would refuse (a retired setting, a removal the registry does not
# know, a setting nothing reads) never reaches the clone deploys run from.
set -euo pipefail

tree="$(git write-tree)"
site="$(git rev-parse --show-toplevel)"
deepops="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../.." && pwd)"
# The hook runs with this repository's GIT_DIR and index; the checks read deepops too.
while IFS='=' read -r name _; do
    unset "${name}"
done < <(env | grep -E '^GIT_[A-Z_]+=' || true)

cd "${deepops}"
DEEPOPS_CONFIG_DIR="${site}" DEEPOPS_SITE_REV="${tree}" exec uv run --quiet python scripts/deepops/config_lint.py \
    redundant dead shadow retired removals
