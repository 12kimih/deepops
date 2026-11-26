#!/usr/bin/env bash
# Helpers for job-scoped epilog cleanup (slurm_epilog_cleanup_scope: job).
#
# Sourced by shared/bin/run-parts.sh and epilog.d/40-lastuserjob-processes.
# Deployed only when the scope is "job".
#
# The question these answer is "is this process in a Slurm job?", asked of
# the node instead of the controller. squeue reports what the controller
# believes, which can lag the node and cannot see a job placed here a moment
# ago. The kernel's /proc/<pid>/cgroup cannot be out of date: Slurm puts every
# process of a job under one job directory, and that is read from the cgroup
# STRUCTURE, never from a name: on cgroup v2 a child of slurmstepd.scope (or
# <node>_slurmstepd.scope) other than "system", slurmstepd's waiting area; on
# cgroup v1 a child of /slurm/uid_N. The directory's name is opaque: job_<id>
# up to Slurm 25.11, the job's SLUID from 26.05 on (2026-11-27: the job_<id>
# match found no job on any node). The same rules, on the same cases
# (tests/lib/slurm_cgroups.json), are cluster_tools.job_cgroup's.
#
# No job id is needed. Before the epilog runs, Slurm's process tracking
# (proctrack/cgroup) has killed the ending job's processes and removed its
# cgroups, so a process of the user still in a job cgroup is another job's, or
# one of the ending job's it could not kill. Either way it is spared and the
# last-user-job cleanup waits for the next epilog.
#
# Everything here fails closed. "Could not tell" is reported as such and the
# caller treats it as "another job may be here": cleanup is skipped and logged,
# never guessed. A skipped cleanup is a delay, since it runs again when the
# next job of the user ends. A wrong guess destroys a running job.
#
# The caller sets epilog_proc_root (normally /proc) before sourcing, so that
# the unit tests can supply a fake process tree. It is set by the script, not
# read from the environment.

: "${epilog_proc_root:?epilog_proc_root must be set by the caller}"

# epilog_resolve_uid
#   Sets epilog_uid from SLURM_JOB_UID, or from the account name when that is
#   missing. Returns 1 when no numeric uid can be established.
epilog_resolve_uid() {
    epilog_uid=""
    if [[ "${SLURM_JOB_UID:-}" =~ ^[0-9]+$ ]]; then
        epilog_uid="$SLURM_JOB_UID"
        return 0
    fi
    local resolved
    if resolved=$(id -u -- "${SLURM_JOB_USER:-}" 2>/dev/null) && [[ "$resolved" =~ ^[0-9]+$ ]]; then
        epilog_uid="$resolved"
        return 0
    fi
    return 1
}

# epilog_pid_owned_by UID PID
#   True when the real or effective uid of PID is UID. /proc/<pid>/status is
#   used rather than the owner of /proc/<pid>, which turns root-owned for a
#   process that has made itself non-dumpable.
#   Returns 0 owned, 1 not owned or gone.
epilog_pid_owned_by() {
    local uid="$1" pid="$2" key real eff rest
    local status="$epilog_proc_root/$pid/status"
    [ -r "$status" ] || return 1
    while read -r key real eff rest; do
        if [ "$key" = "Uid:" ]; then
            [ "$real" = "$uid" ] || [ "$eff" = "$uid" ]
            return
        fi
    done <"$status" 2>/dev/null
    return 1
}

# epilog_cgroup_classify
#   Reads the contents of a /proc/<pid>/cgroup on stdin and prints its kind,
#   then the job cgroup keys, space-separated: "job KEY...", "slurm" (in
#   Slurm's hierarchy, in no job), "none" (outside it) or "bad" (not a cgroup
#   file). Always returns 0.
epilog_cgroup_classify() {
    local line path rest part below kind_slurm=0 keys="" key lines=0 i
    local -a parts
    while IFS= read -r line || [ -n "$line" ]; do
        [ -n "$line" ] || continue
        lines=1
        # hierarchy-id:controllers:path, with an empty controller list on v2.
        if [[ ! "$line" =~ ^[0-9]+:[^:]*:(/.*)$ ]]; then
            printf 'bad\n'
            return 0
        fi
        path="${BASH_REMATCH[1]}"
        IFS=/ read -r -a parts <<<"${path#/}"
        key=""
        rest=slurm_none
        for i in "${!parts[@]}"; do
            part="${parts[i]}"
            if [[ "$part" =~ ^(.+_)?slurmstepd(_.+)?\.scope$ ]]; then
                below="${parts[i + 1]:-system}"
                if [ "$below" = system ]; then rest=slurm; else key="$below"; fi
                break
            fi
        done
        if [ -z "$key" ] && [ "$rest" = slurm_none ] \
            && [[ "${parts[0]-}" =~ ^slurm(_.+)?$ ]]; then
            if [ -n "${parts[2]-}" ] && [[ "${parts[1]}" =~ ^uid_[0-9]+$ ]]; then
                key="${parts[2]}"
            else
                rest=slurm
            fi
        fi
        [ "$rest" != slurm ] || kind_slurm=1
        if [ -n "$key" ] && [[ " $keys " != *" $key "* ]]; then
            keys="${keys:+$keys }$key"
        fi
    done
    if [ "$lines" = 0 ]; then
        printf 'bad\n'
    elif [ -n "$keys" ]; then
        printf 'job %s\n' "$keys"
    elif [ "$kind_slurm" = 1 ]; then
        printf 'slurm\n'
    else
        printf 'none\n'
    fi
}

# epilog_pid_kind PID
#   Prints the kind of PID (epilog_cgroup_classify): job, slurm, none or bad.
#   Returns 0 classified, 1 the process is gone, 2 unreadable or not a cgroup
#   file (printed nothing).
epilog_pid_kind() {
    local pid="$1" file content kind
    file="$epilog_proc_root/$pid/cgroup"
    # The redirect has to wrap the whole command: bash reports a failed read of
    # $(<file) itself, and a process that exits mid-scan is routine.
    if ! { content=$(<"$file"); } 2>/dev/null; then
        [ -e "$file" ] || return 1
        return 2
    fi
    kind=$(epilog_cgroup_classify <<<"$content")
    kind="${kind%% *}"
    [ "$kind" != bad ] || return 2
    printf '%s\n' "$kind"
    return 0
}

# epilog_user_pids UID
#   Prints the pid of every process owned by UID.
#   Returns 0 on success and 2 when the process table is unusable (nothing
#   listed at all), which the caller must treat as "cannot tell".
epilog_user_pids() {
    local uid="$1" entry pid listed=0
    for entry in "$epilog_proc_root"/[0-9]*; do
        [ -d "$entry" ] || continue
        pid="${entry##*/}"
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        listed=1
        if epilog_pid_owned_by "$uid" "$pid"; then
            printf '%s\n' "$pid"
        fi
    done
    [ "$listed" = 1 ] || return 2
    return 0
}

# epilog_other_job_present UID
#   Is any process of UID in a job cgroup? The ending job's are gone by now
#   (see the header), so one there is another job's, or a stuck one of the
#   ending job: either way not the time for the last-user-job cleanup.
#   Returns 0 yes, 1 no, 2 cannot tell (an unreadable cgroup file or one not
#   understood, a process of the user in Slurm's hierarchy but in no job, or
#   no process table).
epilog_other_job_present() {
    local uid="$1" pid pids kind rc unsure=0
    if ! pids=$(epilog_user_pids "$uid"); then
        return 2
    fi
    for pid in $pids; do
        rc=0
        kind=$(epilog_pid_kind "$pid") || rc=$?
        case "$rc" in
            0) ;;
            1) continue ;;
            *) unsure=1; continue ;;
        esac
        case "$kind" in
            job) return 0 ;;
            none) ;;
            *) unsure=1 ;;
        esac
    done
    [ "$unsure" = 0 ] || return 2
    return 1
}
