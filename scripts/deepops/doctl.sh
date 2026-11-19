#!/usr/bin/env bash
# Move nodes between Slurm and Kubernetes, or show which scheduler owns them.
#   doctl.sh NODELIST k8s|slurm|state
set -o pipefail

# Every Slurm and Kubernetes API call is bounded: a timeout per call and a few
# spaced tries, then the script stops with the call that failed.
CALL_TIMEOUT="${DOCTL_CALL_TIMEOUT:-30}"  # seconds per call
TRIES="${DOCTL_TRIES:-3}"
RETRY_DELAY="${DOCTL_RETRY_DELAY:-10}"    # seconds between tries

usage() {
    echo "Usage: $0 NODELIST k8s|slurm|state" >&2
    exit 2
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

# call DESC CMD...: run CMD under the timeout, up to TRIES times. Its output passes
# through; in a command substitution add "|| exit 1", since die only ends the subshell.
call() {
    local desc="$1" try rc
    shift
    for ((try = 1; try <= TRIES; try++)); do
        timeout "${CALL_TIMEOUT}" "$@" && return 0
        rc=$?
        if [ "${rc}" -eq 124 ]; then
            echo "WARN: ${desc}: timed out after ${CALL_TIMEOUT}s (try ${try}/${TRIES})" >&2
        else
            echo "WARN: ${desc}: exit ${rc} (try ${try}/${TRIES})" >&2
        fi
        [ "${try}" -lt "${TRIES}" ] && sleep "${RETRY_DELAY}"
    done
    die "${desc}: failed ${TRIES} times, giving up"
}

kube() {
    call "kubectl $*" kubectl --request-timeout="${CALL_TIMEOUT}s" "$@"
}

# sudo -n never waits on a password prompt; the one prompt is taken up front.
slurm_state() {
    call "scontrol update node=$1 state=$2" sudo -n scontrol update node="$1" state="$2" reason="$3" >/dev/null
}

show() {
    local k8s slurm
    k8s=$(kube get node "$1" --no-headers) || exit 1
    slurm=$(call "sinfo -n $1" sinfo -h -N -n "$1" -o %T) || exit 1
    echo "k8s: $(awk '{print $2}' <<<"${k8s}")"
    echo "slurm: $(head -n 1 <<<"${slurm}")"
}

[ $# -eq 2 ] || usage
case "$2" in k8s | slurm | state) ;; *) usage ;; esac

# Single-field Slurm output: node names only, never parsed from free text.
nodes=$(call "scontrol show hostnames $1" scontrol show hostnames "$1") || exit 1
[ -n "${nodes}" ] || die "no nodes in '$1'"

if [ "$2" != state ]; then
    sudo -v || die "sudo is needed for scontrol update"
fi

for node in ${nodes}; do
    case "$2" in
        k8s)
            echo "${node}: "
            slurm_state "${node}" drain k8s
            kube uncordon "${node}" >/dev/null
            kube label --overwrite node "${node}" scheduler=k8s >/dev/null
            show "${node}"
            ;;
        slurm)
            echo "${node}: "
            kube cordon "${node}" >/dev/null
            slurm_state "${node}" idle slurm
            kube label --overwrite node "${node}" scheduler=slurm >/dev/null
            show "${node}"
            ;;
        state)
            label=$(kube get node "${node}" -o jsonpath='{.metadata.labels.scheduler}') || exit 1
            echo "${node}: ${label}"
            ;;
    esac
done
