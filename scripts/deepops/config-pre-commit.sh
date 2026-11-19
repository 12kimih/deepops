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
# The hook runs with this repository's GIT_DIR and index; the checks read deepops too.
while IFS='=' read -r name _; do
    unset "${name}"
done < <(env | grep -E '^GIT_[A-Z_]+=' || true)

work="$(mktemp -d)"
cleanup() { git -C "${deepops}" worktree remove --force "${work}/deepops" >/dev/null 2>&1 || true; rmdir "${work}" 2>/dev/null || true; }
trap cleanup EXIT
git -C "${deepops}" worktree add --quiet --detach "${work}/deepops" HEAD
# What the checks read but HEAD does not hold: the Kubespray checkout, Galaxy content.
for path in submodules/kubespray roles/galaxy collections; do
    if [ -e "${deepops}/${path}" ]; then
        rmdir "${work}/deepops/${path}" 2>/dev/null || true  # the empty submodule directory
        [ -e "${work}/deepops/${path}" ] || ln -s "${deepops}/${path}" "${work}/deepops/${path}"
    fi
done

cd "${work}/deepops"
DEEPOPS_CONFIG_DIR="${site}" DEEPOPS_SITE_REV="${tree}" \
    "${deepops}/.venv/bin/python" scripts/deepops/config_lint.py redundant dead shadow retired removals
