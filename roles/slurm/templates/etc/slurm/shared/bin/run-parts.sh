#!/usr/bin/env bash
# This could all be done with run-parts using regexes on ubuntu.
# However, centos' version of run-parts is just a simple bash script with no useful flags.
set -e
. '{{ slurm_config_dir }}/shared/lib/log.sh'

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 parts_dir"
    exit 1
fi
parts_dir=$1

# Logging never fails the run: a missing /dev/log (journald restarting) must not drain
# the node.
log () {
    part_log slurm "$@"
}

# The node as Slurm names it, which need not be the host name.
node_name="${SLURMD_NODENAME:-$(hostname -s)}"
# Every controller call is bounded: a slurmctld that does not answer must cost a
# skipped cleanup, never a prolog that hangs every job start on this node.
squeue_timeout=15

# Use an absolute path: slurmd's PATH does not include a custom
# slurm_install_prefix, and with an empty command result both sides of the
# comparison below were "" and every job was treated as exclusive.
squeue_bin="{{ slurm_install_prefix }}/bin/squeue"
# Where each part's output goes (slurm_log_files in roles/slurm_paths).
parts_log="{{ slurm_log_files['slurm-prolog-epilog'] }}"

# The kernel's view of whose processes these are (shared/lib/epilog-job-scope.sh):
# every process of a Slurm job sits in a job_<id> cgroup.
epilog_proc_root=/proc
job_scope_lib='{{ slurm_config_dir }}/shared/lib/epilog-job-scope.sh'

# Does the job have the node to itself? Only then may the *-exclusive-* parts run: they
# act on the whole node (every GPU's power and clocks, the CPU governor, the page
# cache). Slurm's own answer is the job's Exclusive field (squeue -O Exclusive), NODE for
# a whole-node allocation (--exclusive, or a partition with Exclusive=NODE).
# OverSubscribe (%h) "does not encode exclusivity" (man squeue): on a partition with
# OverSubscribe=NO every job reads NO, and taking that for exclusive ran these parts for
# every job on shared nodes (2026-11-23: other jobs' GPU processes were killed).
# The prolog asks and leaves a record on the node; the epilog undoes what the prolog did
# by that record, never by asking again: a squeue that timed out in the epilog would
# leave the GPUs at their max levels and the governor at performance. A context slurmd
# does not name (an older slurmd) asks in both. Asked only when the parts directory has
# such parts. The lookup runs inside a conditional because of "set -e": a failed squeue
# must skip the parts, not fail the job.
exclusive=0
exclusive_record='{{ slurm_exclusive_run_dir }}/job-'"${SLURM_JOB_ID}"
if compgen -G "$parts_dir/*-exclusive-*" >/dev/null; then
    if [ "${SLURM_SCRIPT_CONTEXT:-}" = epilog_slurmd ]; then
        if [ -e "$exclusive_record" ]; then
            exclusive=1
        fi
    elif job_exclusive=$(timeout "$squeue_timeout" "$squeue_bin" -h -j "$SLURM_JOB_ID" -O Exclusive 2>/dev/null); then
        case "$(printf '%s' "$job_exclusive" | tr -d '[:space:]')" in
            NODE) exclusive=1 ;;
            NO|USER|MCS|TOPO) ;;
            *) log "[WARN] squeue returned no usable Exclusive for job ${SLURM_JOB_ID} ('${job_exclusive}'); not running the node-wide parts." ;;
        esac
        if [ "$exclusive" = 1 ] && [ "${SLURM_SCRIPT_CONTEXT:-}" = prolog_slurmd ]; then
            # Without the record the epilog would not undo the parts: run them only if
            # it is written.
            if ! { mkdir -p '{{ slurm_exclusive_run_dir }}' && : > "$exclusive_record"; } 2>/dev/null; then
                exclusive=0
                log "[WARN] cannot record job ${SLURM_JOB_ID} as exclusive in {{ slurm_exclusive_run_dir }}; not running the node-wide parts."
            fi
        fi
    else
        log "[WARN] squeue failed for job ${SLURM_JOB_ID}; not running the node-wide parts."
    fi
fi

# Find out if there are any more jobs on this node for this user.
# Same reasoning as above with one difference: a failed lookup must not be read
# as "no other jobs", because that would run the *-lastuserjob-* cleanup scripts
# while another job of the same user is still on the node. Piping squeue into
# "wc -l" also hides its exit status, so the status is captured explicitly.
#
# RUNNING alone is too narrow. A job that is SUSPENDED (scontrol suspend, or
# preempted with SuspendTime) or STOPPED still owns its processes, its files in
# /tmp and /dev/shm, and its enroot directories; a job that is CONFIGURING or
# RESIZING has an allocation on this node and is about to. None of those appear
# under "-t running", so the *-lastuserjob-* scripts would run and reap them.
# The states are the ones that hold a node (cluster_tools vocab.JOB_HOLDS, the
# list the maintenance drain waits on), less one:
#
# COMPLETING is deliberately absent. The epilog runs while its own job is in
# that state, so including it would make every job find itself, leave
# last_user_job at 0, and disable the cleanup entirely.
lastuserjob_states='{{ 'holding' | slurm_job_states | reject('eq', 'COMPLETING') | join(',') | lower }}'
last_user_job=0
# Asked only when the parts directory has such parts (the epilog's; not the prolog's).
if ! compgen -G "$parts_dir/*-lastuserjob-*" >/dev/null; then
    :
elif user_jobs=$(timeout "$squeue_timeout" "$squeue_bin" -h -u "$SLURM_JOB_USER" -w "$node_name" -t "$lastuserjob_states" -o '%i' 2>/dev/null); then
    # Drop this job's own entry defensively. It should not be in the states
    # above, but an id match is cheap and keeps a future state addition from
    # silently switching the cleanup off.
    other_jobs=$(printf '%s\n' "$user_jobs" | grep -vxF "$SLURM_JOB_ID" || :)
    if [ -z "$other_jobs" ]; then
        last_user_job=1
    fi
else
    log "[WARN] squeue failed while counting jobs for ${SLURM_JOB_USER} on ${node_name}; not running the last-user-job scripts."
fi

{% if slurm_epilog_cleanup_scope == 'job' %}
# Job-scoped cleanup (slurm_epilog_cleanup_scope: job). Only meaningful where
# there are *-lastuserjob-* scripts, i.e. the epilog; the prolog skips it.
#
# squeue is the controller's opinion of the node, and it can be behind it: with
# the default CompleteWait=0 the controller may place a new job of this user
# here after squeue answered and before the cleanup below runs. The kernel's
# view cannot be: every process of a Slurm job sits in a job_<id> cgroup, so a
# process of this user in the cgroup of any job other than the one ending is
# direct, node-local evidence that the user is not done here. Either source
# saying "another job is here" is enough to skip the *-lastuserjob-* scripts.
#
# When the local view cannot be established (no numeric uid, unreadable or
# unrecognised /proc/<pid>/cgroup data) the answer is the safe one: another job
# may be here. The cleanup is deferred to the next job's epilog; it is never
# guessed at.
# node_view: clear (no other job's process found), other_job, or unknown.
node_view=clear
if compgen -G "$parts_dir/*-lastuserjob-*" >/dev/null; then
    node_view=unknown
    # `.` on a missing file must not take the whole epilog down under set -e.
    if [ -r "$job_scope_lib" ] && . "$job_scope_lib"; then
        if epilog_resolve_uid; then
            node_rc=0
            epilog_other_job_present "$epilog_uid" "$SLURM_JOB_ID" || node_rc=$?
            case "$node_rc" in
                0) node_view=other_job
                   log "Another job of ${SLURM_JOB_USER} has processes on this node (found by cgroup); not running the last-user-job scripts."
                   ;;
                1) node_view=clear ;;
                *) log "[WARN] could not read the cgroup membership of the processes of ${SLURM_JOB_USER}; assuming another job may be here." ;;
            esac
        else
            log "[WARN] no numeric uid for ${SLURM_JOB_USER}; assuming another job may be here."
        fi
    else
        log "[WARN] cannot load ${job_scope_lib}; assuming another job of ${SLURM_JOB_USER} may be here."
    fi
fi
if [ "$node_view" != clear ]; then
    last_user_job=0
fi
# Scripts that decide per process (40-lastuserjob-processes) read this to know
# whether the user has no other job here, which is when orphans may be reaped.
export EPILOG_LAST_USER_JOB="$last_user_job"

{% endif %}
# Re-implement run-parts since on centos it is just a bash script with no useful flags.
failed=0
for script in "$parts_dir"/*; do
    if [ ! -x "$script" ]; then
        log "Skipping $script because it is not executable."
        continue
    fi
    if [[ "$script" == *"-exclusive-"* ]] && [ "$exclusive" = 0 ]; then
        log "Skipping $script because the job does not have the node to itself."
        continue
    fi
{% if slurm_epilog_cleanup_scope == 'job' %}
    # 40-lastuserjob-processes classifies each process by its own job cgroup
    # and applies the last-user-job rule to orphans itself, so it must also run
    # when another job is present: it is what spares that job's processes and
    # still reaps the ending job's.
    if [[ "$script" == *"-lastuserjob-processes" ]]; then
        log "Running $script ..."
        if ! $script >>"$parts_log" 2>&1; then
            log "[ERROR] $script failed. Check the log at $parts_log for more details."
            failed=1
        fi
        continue
    fi
{% endif %}
    if [[ "$script" == *"-lastuserjob-"* ]] && [ "$last_user_job" = 0 ]; then
        log "Skipping $script because there is still another job running on this node for the same user."
        continue
    fi
    log "Running $script ..."
    if ! $script >>"$parts_log" 2>&1; then
        log "[ERROR] $script failed. Check the log at $parts_log for more details."
        failed=1
    fi
done

# The job's exclusive record has served the epilog.
if [ "${SLURM_SCRIPT_CONTEXT:-}" = epilog_slurmd ]; then
    rm -f "$exclusive_record" || :
fi

if [ "$failed" = "1" ]; then
    log "One or more scripts failed."
    exit 1
fi
