#!/usr/bin/env bash
# Every repository check, in one place: pre-commit, CI and people all run this.
#   scripts/check.sh          everything
#   scripts/check.sh --fast   the quick subset pre-commit runs on each commit (style,
#                             generated docs, linters, sensitive data, the unit suites
#                             not marked .slow); CI runs everything
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
step "generated dashboards are current" "${python}" scripts/dashboards/build.py --check
step "people rosters are valid (config.example, and config if present)" "${python}" scripts/deepops/people.py check
step "config mirrors config.example, nothing set goes unread, docs name real paths" "${python}" scripts/deepops/config_lint.py
# Linters judge the files git tracks, staged ones included (git add a new file to have it
# checked): what CI checks and what a commit contains, never another session's
# half-written file in a shared working tree. .gitignore keeps the rest out.
tracked() {
    local f
    while IFS= read -r -d '' f; do [ -f "$f" ] && printf '%s\0' "$f"; done < <(git ls-files -z --cached -- "$@")
}
mapfile -d '' yaml_files < <(tracked '*.yml' '*.yaml')
mapfile -d '' py_files < <(tracked '*.py' '*.pyi')
step "yamllint" yamllint --no-warnings "${yaml_files[@]}"
step "ruff" ruff check --force-exclude "${py_files[@]}"
step "ruff format (run: uv run ruff format)" ruff format --check --force-exclude "${py_files[@]}"
step "no secrets or site data in tracked files" "${python}" scripts/deepops/sensitive_guard.py tree

# Unit suites: the quick ones also run in --fast (the commit hook); a suite marks
# itself slow with a .slow file and then runs only in the full check. They run without
# the GIT_* variables a git hook exports, so a test that drives git in a temporary
# repository cannot reach this repository's index.
isolated=(env -u GIT_DIR -u GIT_INDEX_FILE -u GIT_WORK_TREE -u GIT_PREFIX -u GIT_OBJECT_DIRECTORY -u GIT_ALTERNATE_OBJECT_DIRECTORIES -u GIT_COMMON_DIR)
# Suites are the tracked tests/<suite>/test_*.py, like every other check.
mapfile -t suites < <(git ls-files -- 'tests/*/test_*.py' | cut -d/ -f1-2 | sort -u | sed 's|$|/|')
for suite in "${suites[@]}"; do
    if "${fast}" && [ -e "${suite}.slow" ]; then continue; fi
    step "unit tests (${suite%/})" "${isolated[@]}" bash -c "cd '${suite}' && ${python} -m unittest discover -s . -q"
done

if ! "${fast}"; then
    step "unit tests (scripts/validation/tests)" "${isolated[@]}" "${python}" -m unittest discover -s scripts/validation/tests -q
    step "ansible-lint" ansible-lint -q
    for playbook in playbooks/slurm-cluster.yml playbooks/k8s-cluster.yml; do
        step "syntax-check ${playbook}" ansible-playbook -i config.example/inventory --syntax-check "${playbook}"
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
