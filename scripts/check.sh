#!/usr/bin/env bash
# Every repository check, in one place: pre-commit, CI and people all run this.
#   scripts/check.sh          everything
#   scripts/check.sh --fast   the quick subset pre-commit runs on each commit (style,
#                             generated docs, yamllint); CI runs everything
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
fast=false
[ "${1:-}" = "--fast" ] && fast=true

failed=()
step() {
    local name="$1"; shift
    echo "==> ${name}"
    if "$@"; then echo "    ok"; else echo "    FAILED"; failed+=("${name}"); fi
}

# Run under uv, which first makes .venv match uv.lock, so the tools are always the
# pinned ones however this script was started.
if [ -z "${DEEPOPS_CHECK_UV:-}" ]; then
    command -v uv >/dev/null || { echo "uv is required; run scripts/setup.sh." >&2; exit 2; }
    DEEPOPS_CHECK_UV=1 exec uv run --locked -- scripts/check.sh "$@"
fi
python=python3

step "ansible.cfg has only known settings" ansible-config validate
step "Galaxy collections and roles match roles/requirements.yml" "${python}" scripts/deepops/galaxy.py --check
step "style is normalized" "${python}" scripts/deepops/normalize-ansible-style.py --check .
step "generated docs are current" "${python}" scripts/docs/generate.py --check
step "yamllint" yamllint --no-warnings .

if ! "${fast}"; then
    step "unit tests (scripts/validation/tests)" "${python}" -m unittest discover -s scripts/validation/tests -q
    step "ansible-lint" ansible-lint -q
    for playbook in playbooks/slurm-cluster.yml playbooks/k8s-cluster.yml; do
        step "syntax-check ${playbook}" ansible-playbook -i config.example/inventory --syntax-check "${playbook}"
    done
    for suite in tests/*/; do
        if compgen -G "${suite}test_*.py" >/dev/null; then
            step "unit tests (${suite%/})" bash -c "cd '${suite}' && ${python} -m unittest discover -s . -q"
        fi
    done
    step "slurm epilog tests" bash tests/slurm-epilog/run-tests.sh
    for plugin in config.example/files/slurm/job_submit.lua; do
        step "job_submit.lua tests" lua roles/slurm/tests/job_submit_test.lua "${plugin}"
    done
fi

if [ "${#failed[@]}" -gt 0 ]; then
    printf 'Failed: %s\n' "${failed[@]}" >&2
    exit 1
fi
echo "All checks passed."
