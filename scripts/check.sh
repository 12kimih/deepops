#!/usr/bin/env bash
# Every repository check, in one place: pre-commit, CI and people all run this.
#   scripts/check.sh          everything
#   scripts/check.sh --fast   the quick subset pre-commit runs on each commit (style,
#                             generated docs, linters, sensitive data, the unit suites
#                             not marked .slow); CI runs everything
#   scripts/check.sh --missing-tools   the packages of the tools it needs that are missing
#
# The checks are independent, so they run in parallel (CHECK_JOBS at a time, default
# the CPU count): the run takes about as long as its slowest check, not their sum.
# Each prints one line when it ends, with its time; a failed one then prints its
# output in full. CHECK_JOBS=1 runs them one by one. They run niced, at idle I/O
# priority: the host may be a login node or the controller, whose users and services
# come first.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# The commands the full check runs from outside .venv, as <command>:<Debian/Ubuntu
# package>. scripts/setup.sh and CI install the missing ones (--missing-tools prints
# their packages); golang-go may be older than go.mod asks, go then fetches that toolchain.
tools=(lua5.4:lua5.4 go:golang-go gofmt:golang-go)
missing_tools() {
    local tool seen=" "
    for tool in "${tools[@]}"; do
        command -v "${tool%%:*}" >/dev/null && continue
        [[ "${seen}" == *" ${tool#*:} "* ]] || { seen+="${tool#*:} "; echo "${tool#*:}"; }
    done
}
if [ "${1:-}" = "--missing-tools" ]; then missing_tools; exit 0; fi
fast=false
[ "${1:-}" = "--fast" ] && fast=true

# Run under uv, which first makes .venv match uv.lock, so the tools are always the
# pinned ones however this script was started.
if [ -z "${DEEPOPS_CHECK_UV:-}" ]; then
    command -v uv >/dev/null || { echo "uv is required; run scripts/setup.sh." >&2; exit 2; }
    idle=(); command -v ionice >/dev/null && idle=(ionice -c 3)   # Linux; elsewhere plain
    DEEPOPS_CHECK_UV=1 exec "${idle[@]}" uv run --locked -- scripts/check.sh "$@"
fi
python=python3

names=()
commands=()
# step NAME COMMAND...: one check; it runs later, with the others.
step() {
    names+=("$1"); shift
    commands+=("$(printf '%q ' "$@")")
}

step "ansible.cfg has only known settings" ansible-config validate
step "Galaxy collections and roles match roles/requirements.yml" "${python}" scripts/deepops/galaxy.py --check
step "style is normalized" "${python}" scripts/deepops/normalize-ansible-style.py --check .
step "generated docs are current" "${python}" scripts/docs/generate.py --check
step "generated dashboards are current" "${python}" scripts/dashboards/build.py --check
step "role templates parse as Jinja" "${python}" scripts/deepops/check-template-syntax.py
step "people rosters are valid (config.example, and config if present)" "${python}" scripts/deepops/people.py check
step "configs state only what differs, nothing set goes unread, docs name real paths and settings" "${python}" scripts/deepops/config_lint.py
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
# Every GIT_* variable, not a list of names: GIT_CONFIG_PARAMETERS, GIT_CONFIG_COUNT and
# GIT_CONFIG_KEY_n carry `git -c` settings into every git the tests run.
isolated=(env)
while IFS= read -r name; do isolated+=(-u "${name}"); done < <(compgen -e | grep '^GIT_' || true)
# The tracked tests/<suite>/test_*.py, like every other check, one check per file so a
# slow suite's files run side by side; each runs from its suite directory, as discover
# would run it.
# Through tests/lib/strict_unittest.py: unittest that also fails on a ResourceWarning.
strict="$(pwd)/tests/lib/strict_unittest.py"
mapfile -d '' -t test_files < <(git ls-files -z -- 'tests/*/test_*.py')
for test_file in "${test_files[@]}"; do
    suite="$(dirname "${test_file}")"
    if "${fast}" && [ -e "${suite}/.slow" ]; then continue; fi
    module="$(basename "${test_file}" .py)"
    step "unit tests (${suite}/${module})" "${isolated[@]}" bash -c "cd '${suite}' && ${python} '${strict}' -q '${module}'"
done

if ! "${fast}"; then
    mapfile -t missing < <(missing_tools)
    if [ "${#missing[@]}" -gt 0 ]; then
        echo "Missing tools; run scripts/setup.sh, or: sudo apt-get install ${missing[*]}" >&2
        exit 2
    fi
    step "unit tests (scripts/validation/tests)" "${isolated[@]}" "${python}" "${strict}" discover -s scripts/validation/tests -q
    step "ansible-lint" ansible-lint -q
    for playbook in playbooks/slurm-cluster.yml playbooks/k8s-cluster.yml; do
        step "syntax-check ${playbook}" ansible-playbook -i config.example/inventory --syntax-check "${playbook}"
    done
    step "slurm epilog tests" bash tests/slurm-epilog/run-tests.sh
    # Fake helm and kubectl; about eight minutes, so the full check only.
    step "runai role tests" bash tests/runai/run-tests.sh
    step "job_submit.lua tests (Lua 5.4, as slurmctld runs it)" \
        bash -c 'set -euo pipefail; out=$(mktemp); trap "rm -f $out" EXIT
            "$1" roles/slurm/tests/render_job_submit.py roles/slurm/tests/job_submit_policy.yml > "$out"
            lua5.4 roles/slurm/tests/job_submit_test.lua "$out"' _ "${python}"
    # The Slurm exporter is Go; nothing else would run its tests.
    step "Slurm exporter: gofmt, vet, tests" bash -c \
        'cd roles/prometheus-slurm-exporter/files/exporter && test -z "$(gofmt -l .)" && go vet ./... && go test ./...'
fi

# Run them: each in the background with its own log, at most CHECK_JOBS at once.
jobs_max="${CHECK_JOBS:-$(nproc 2>/dev/null || echo 4)}"
logs="$(mktemp -d)"
trap 'kill $(jobs -p) 2>/dev/null || true; rm -rf "${logs}"' EXIT
# The test playbooks are not runs to keep: no callback plugin directory, no ARA record;
# and they need no site secret: a throwaway vault password file for the one ansible.cfg
# names, so the checks run where no site config is (scripts/deepops/unrecorded.py, the
# same for a test run on its own).
mkdir "${logs}/no-callbacks"
export ANSIBLE_CALLBACK_PLUGINS="${logs}/no-callbacks"
(umask 077 && od -An -N16 -tx1 /dev/urandom | tr -d ' \n' >"${logs}/vault-pass")
export ANSIBLE_VAULT_PASSWORD_FILE="${logs}/vault-pass"
run_one() {
    local i="$1" start=${SECONDS} status=ok
    nice -n 10 bash -c "${commands[$i]}" >"${logs}/${i}" 2>&1 </dev/null || status=FAILED
    echo "${status}" >"${logs}/${i}.status"
    printf '%-6s %4ss  %s\n' "${status}" "$((SECONDS - start))" "${names[$i]}"
}
echo "Running ${#names[@]} checks, ${jobs_max} at a time"
started=${SECONDS}
for i in "${!names[@]}"; do
    while [ "$(jobs -rp | wc -l)" -ge "${jobs_max}" ]; do wait -n || true; done
    run_one "${i}" &
done
wait

failed=()
for i in "${!names[@]}"; do
    if [ "$(cat "${logs}/${i}.status" 2>/dev/null)" != ok ]; then
        failed+=("${names[$i]}")
        printf '\n==> %s: FAILED\n' "${names[$i]}"
        cat "${logs}/${i}"
    fi
done

if [ "${#failed[@]}" -gt 0 ]; then
    printf 'Failed: %s\n' "${failed[@]}" >&2
    exit 1
fi
echo "All checks passed ($((SECONDS - started))s)."
