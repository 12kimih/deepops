#!/usr/bin/env bash
# Regression tests for the Slurm epilog cleanup scope.
#
# The slurm role is excluded from the molecule CI because it needs systemd
# services a container cannot run, so these scripts have had no automated
# cover. What they do is destructive -- `killall -9 -u`, `rm -fr` -- and the
# failure mode is silent: a cleanup that reaps a bystander looks exactly like a
# cleanup that worked. The cases below pin the guards that decide *whether* to
# reap; they need neither Slurm nor root.
#
# The templates are rendered first (render.py) so that what runs here is the
# form a deployment gets, not a trimmed copy.
#
# Usage: tests/slurm-epilog/run-tests.sh
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
templates="$repo/roles/slurm/templates/etc/slurm"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

pass=0
fail=0

ok() { printf '  ok   %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL %s\n' "$1"; printf '       %s\n' "${2-}"; fail=$((fail + 1)); }

# The scripts under test log with `logger`, and they run under `set -e`. On a
# host with no syslog socket -- a CI container, most build agents -- `logger`
# exits non-zero and takes the script down with it, at whatever line it had
# reached. That turns "the guard refused to delete" and "the script died before
# it got there" into the same observation, which is the one distinction these
# tests exist to make. Put a stub on PATH so the log still reaches stderr, where
# the assertions read it, without the exit status.
write_logger_stub() {
    # $1 = directory to place bin/logger in
    mkdir -p "$1/bin"
    cat > "$1/bin/logger" <<'STUB'
#!/usr/bin/env bash
tag=""
while [ $# -gt 0 ]; do
    case "$1" in
        -t) tag="$2"; shift 2 ;;
        -s) shift ;;
        --) shift; break ;;
        -*) shift ;;
        *) break ;;
    esac
done
printf '%s: %s\n' "${tag:-logger}" "$*" >&2
exit "${LOGGER_RC:-0}"  # a case sets 1: syslog unreachable
STUB
    chmod +x "$1/bin/logger"
}

render() {
    # $1 = template path relative to $templates, $2 = output path
    # Per-job private directories are off unless a case names them (RENDER_PRIVATE_DIRS,
    # comma-separated): with them on, the job-scope file cleanup never runs at all.
    python3 "$here/render.py" "$templates/$1" "$2" "${RENDER_SCOPE:-job}" "${RENDER_PRIVATE_DIRS-}" || return 1
    # Every part logs through shared/lib/log.sh: point it at the harness's copy.
    sed -i "s#/etc/slurm/shared/lib/log.sh#$work/shared-lib/log.sh#" "$2"
    chmod +x "$2"
    # A rendered script that does not parse would fail every behavioural case
    # below with the same opaque message; check the syntax separately so the
    # cause is named.
    bash -n "$2"
}


mkdir -p "$work/shared-lib"
render "shared/lib/log.sh" "$work/shared-lib/log.sh"

# --- logging never fails a part -------------------------------------------------------
# Under "set -e" a logger that cannot reach syslog (journald restarting) exited 1, failed
# the part and drained the node; every part logs through part_log, which cannot fail.
echo
echo "shared/lib/log.sh -- logging never fails a part"
write_logger_stub "$work/logstub"
if out=$(PATH="$work/logstub/bin:$PATH" LOGGER_RC=1 bash -ec ". '$work/shared-lib/log.sh'; part_log t hello; echo after" 2>&1) \
   && grep -q '^after$' <<<"$out" && grep -q 'hello' <<<"$out"; then
    ok "part_log returns 0 and still writes the message when logger fails"
else
    no "part_log returns 0 and still writes the message when logger fails" "$out"
fi
direct=$(grep -rlE '(^|[^_[:alnum:]])logger[[:space:]]' "$templates" | grep -v '/shared/lib/log.sh$' || :)
if [ -z "$direct" ]; then
    ok "no prolog/epilog template calls logger except through part_log"
else
    no "no prolog/epilog template calls logger except through part_log" "$direct"
fi

# A fake /proc: <root>/<pid>/status carries the Uid line and <root>/<pid>/cgroup
# the cgroup membership, which is all the job-scoped helpers read. Paths are
# written the way the kernel writes them, for cgroup v1 and v2.
fake_proc() {
    # $1 = root, $2 = pid, $3 = uid, $4 = contents of /proc/<pid>/cgroup
    mkdir -p "$1/$2"
    printf 'Name:\tfixture\nUid:\t%s\t%s\t%s\t%s\n' "$3" "$3" "$3" "$3" > "$1/$2/status"
    if [ -n "${4-}" ]; then
        printf '%s\n' "$4" > "$1/$2/cgroup"
    else
        : > "$1/$2/cgroup"
    fi
}

# cgroup lines as Slurm lays them out. v1 has one line per controller
# hierarchy under /slurm/uid_N/job_M; v2 has one unified line under the
# slurmstepd scope, the job's directory named job_<id> up to Slurm 25.11 and by
# its SLUID from 26.05 on. The full spec is tests/lib/slurm_cgroups.json.
cg_v1_job() { printf '4:cpuset:/slurm/uid_%s/job_%s/step_0/task_0\n3:memory:/slurm/uid_%s/job_%s/step_0\n1:name=systemd:/system.slice/slurmd.service' "$1" "$2" "$1" "$2"; }
cg_v2_job() { printf '0::/system.slice/slurmstepd.scope/job_%s/step_0/user/task_0' "$1"; }
cg_v2_sluid() { printf '0::/system.slice/slurmstepd.scope/%s/step_batch/user/task_0' "$1"; }
cg_none()   { printf '0::/user.slice/user-%s.slice/session-3.scope' "$1"; }

# ---------------------------------------------------------------------------
# run-parts.sh: which jobs count as "the user still has work on this node"
# ---------------------------------------------------------------------------
#
# The harness drives the real script with a stub squeue on PATH. The stub reads
# its scripted answer from $SQUEUE_FIXTURE so each case controls both the
# output and the exit status.

setup_runparts() {
    local dir="$work/runparts"
    rm -rf "$dir"
    mkdir -p "$dir/bin" "$dir/parts"

    render "shared/bin/run-parts.sh" "$dir/run-parts.sh" || return 1

    # Job scope: the dispatcher also reads the node's own process table. Point
    # it at a fake /proc and at the helper library rendered into the fixture,
    # and check the redirects took, as the cleanup setup does: a template edit
    # that changes those lines would otherwise hand the real /proc back.
    mkdir -p "$dir/proc"
    if [ "${RENDER_SCOPE:-job}" = job ]; then
        mkdir -p "$dir/lib"
        render "shared/lib/epilog-job-scope.sh" "$dir/lib/epilog-job-scope.sh" || return 1
        sed -i "s#^epilog_proc_root=.*#epilog_proc_root='$dir/proc'#" "$dir/run-parts.sh"
        sed -i "s#^job_scope_lib=.*#job_scope_lib='$dir/lib/epilog-job-scope.sh'#" "$dir/run-parts.sh"
        if ! grep -qF "epilog_proc_root='$dir/proc'" "$dir/run-parts.sh" \
           || ! grep -qF "job_scope_lib='$dir/lib/epilog-job-scope.sh'" "$dir/run-parts.sh"; then
            printf '  FAIL harness: the dispatcher /proc root was not redirected into the fixture\n' >&2
            return 1
        fi
        # Every real /proc has processes. One owned by another account keeps an
        # otherwise empty fixture from reading as "no process table".
        fake_proc "$dir/proc" 1 0 '0::/init.scope'
    fi

    # The script calls squeue twice: once for the jobs on the node (-w only; the
    # node-wide *-exclusive-* parts run only for the one job there), once for the
    # user's jobs on the node (-u). $SQUEUE_NODE_JOBS answers the first (default: the
    # same list as $SQUEUE_JOBS), so a case can put a job of another user on the node.
    # The stub must honour -t. A stub that answers the same way whatever states
    # were asked for would let the pre-fix script pass the SUSPENDED case too,
    # and the test would prove nothing. $SQUEUE_JOBS is "id:STATE" per line and
    # the stub returns only the ids whose state was requested -- which is what
    # the real squeue does, and what the fix depends on.
    cat > "$dir/bin/squeue" <<'STUB'
#!/usr/bin/env bash
want_states=""
prev=""
by_user=""
for arg in "$@"; do
    if [ "$arg" = "Exclusive" ]; then
        echo "${SQUEUE_EXCLUSIVE:-NO}"  # Slurm's Exclusive field: NODE for --exclusive
        exit "${SQUEUE_EXCLUSIVE_RC:-0}"
    fi
    case "$arg" in -u|--user|--user=*) by_user=1 ;; esac
    case "$prev" in
        -t|--states) want_states="$arg" ;;
    esac
    case "$arg" in
        -t*) [ "$arg" != "-t" ] && want_states="${arg#-t}" ;;
        --states=*) want_states="${arg#--states=}" ;;
    esac
    prev="$arg"
done

if [ -n "${SQUEUE_RC:-}" ] && [ "$SQUEUE_RC" != 0 ]; then
    exit "$SQUEUE_RC"
fi

jobs="${SQUEUE_JOBS:-}"
[ -n "$by_user" ] || jobs="${SQUEUE_NODE_JOBS-$jobs}"
[ -n "$jobs" ] || exit 0

printf '%s\n' "$jobs" | while IFS=: read -r id state; do
    [ -n "$id" ] || continue
    # No -t at all means squeue's default; the scripts under test always pass
    # one, so treat a missing filter as "match everything" rather than guess.
    if [ -z "$want_states" ]; then
        printf '%s\n' "$id"
        continue
    fi
    case ",${want_states}," in
        *",${state},"*) printf '%s\n' "$id" ;;
    esac
done
exit 0
STUB
    chmod +x "$dir/bin/squeue"

    write_logger_stub "$dir"

    # The script resolves squeue as {{ slurm_install_prefix }}/bin/squeue, so
    # the stub has to sit at that absolute path rather than merely on PATH.
    mkdir -p "$dir/usr/bin"
    cp "$dir/bin/squeue" "$dir/usr/bin/squeue"
    sed -i "s#^squeue_bin=.*#squeue_bin=\"$dir/usr/bin/squeue\"#" "$dir/run-parts.sh"

    # run-parts.sh appends every child script's output to a fixed path under
    # /var/log/slurm. A machine running the tests need not have it, and a
    # failed redirect makes the child look like it failed, so send it to the
    # fixture instead.
    mkdir -p "$dir/log"
    sed -i "s#/var/log/slurm/prolog-epilog#$dir/log/prolog-epilog#" "$dir/run-parts.sh"

    # A marker script that records whether it ran. The -lastuserjob- infix is
    # what run-parts.sh gates on.
    cat > "$dir/parts/42-lastuserjob-marker" <<STUB
#!/usr/bin/env bash
touch "$dir/RAN"
STUB
    chmod +x "$dir/parts/42-lastuserjob-marker"

    printf '%s' "$dir"
}

runparts_case() {
    # $1 = label, $2 = expected ran|skipped, $3 = jobs as "id:STATE" lines,
    # $4 = SQUEUE_RC
    local label="$1" expect="$2" jobs="${3-}" rc="${4-0}"
    local dir
    dir="$(setup_runparts)" || { no "$label" "harness setup failed"; return; }
    # $5 = optional function that builds the fake /proc under $dir/proc
    if [ -n "${5-}" ]; then "$5" "$dir/proc"; fi

    rm -f "$dir/RAN"
    local out
    out=$(
        PATH="$dir/bin:$PATH" \
        SLURM_JOB_USER=alice SLURM_JOB_UID=1001 SLURM_JOB_ID=1000 SLURMD_NODENAME=node1 \
        HOSTNAME=node1 SLURM_JOBID=1000 \
        SQUEUE_JOBS="$jobs" SQUEUE_RC="$rc" \
        "$dir/run-parts.sh" "$dir/parts" 2>&1
    )

    local actual=skipped
    [ -e "$dir/RAN" ] && actual=ran

    if [ "$actual" = "$expect" ]; then
        ok "$label"
    else
        no "$label" "expected $expect, got $actual -- $(printf '%s' "$out" | tail -2 | tr '\n' ' ')"
    fi
}

echo "run-parts.sh -- last-user-job gate"
runparts_case "no other job of this user runs the cleanup"            ran     ""
runparts_case "another RUNNING job blocks the cleanup"                skipped "1001:running"
# The reason this issue exists: a suspended job owns processes and files but
# never appeared under "-t running".
runparts_case "a SUSPENDED job blocks the cleanup"                    skipped "1002:suspended"
runparts_case "a CONFIGURING job blocks the cleanup"                  skipped "1003:configuring"
# A failed lookup is not evidence of absence.
runparts_case "squeue failure blocks the cleanup"                     skipped "" 1
# The epilog's own job must not count as "another job", or the cleanup would
# never run again.
runparts_case "the job's own id does not block its cleanup"           ran     "1000:running"
runparts_case "own id plus a sibling job still blocks"                skipped "1000:running
1004:running"

# ---------------------------------------------------------------------------
# 42-lastuserjob-cleanup: whose files may be deleted
# ---------------------------------------------------------------------------

# A deterministic identity that resolves on this host and is not root. The
# script skips root by design, so a test that runs as root has to borrow an
# unprivileged account; everyone else can just use their own, whose files they
# can create without chown.
#
# This matters more than it looks. With an unresolvable name the script exits at
# the "cannot resolve" guard before reaching anything else, so a case meant to
# prove the exemption works passes even with the exemption deleted.
cleanup_identity() {
    if [ "$(id -u)" != 0 ]; then
        id -un
        return 0
    fi
    for candidate in nobody rocky ubuntu daemon; do
        if id -u -- "$candidate" >/dev/null 2>&1; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    return 1
}

# Every part that spares operators sources shared/lib/localusers.sh: render it beside the
# part, point it at the fixture's list, and the part at it (verified, not assumed).
use_localusers() {
    # $1 = rendered part, $2 = the fixture's localusers.backup
    local lib
    lib="$(dirname "$1")/localusers.sh"
    render "shared/lib/localusers.sh" "$lib" || return 1
    sed -i "s#^localusers_backup=.*#localusers_backup='$2'#" "$lib"
    sed -i "s#/etc/slurm/shared/lib/localusers.sh#$lib#" "$1"
    grep -qF ". '$lib'" "$1"
}

own_file() {
    # $1 = owner, $2 = path
    : > "$2" || return 1
    [ "$(id -u)" = 0 ] || return 0
    chown "$1" "$2"
}

own_dir() {
    # $1 = owner, $2 = path
    mkdir -p "$2" || return 1
    [ "$(id -u)" = 0 ] || return 0
    chown "$1" "$2"
}

setup_cleanup() {
    local dir="$work/cleanup"
    rm -rf "$dir"
    mkdir -p "$dir/etc/slurm" "$dir/roots/tmp" "$dir/roots/shm"
    write_logger_stub "$dir"

    render "epilog.d/42-lastuserjob-cleanup" "$dir/42-cleanup" || return 1
    use_localusers "$dir/42-cleanup" "$dir/etc/slurm/localusers.backup" || return 1

    # The script deletes under /tmp and /dev/shm. Leaving those pointed at the
    # real directories means that the moment a guard regresses, the suite
    # deletes a real account's scratch and only then reports the failure. Every
    # case gets the fixture roots, including the ones that are supposed to stop
    # before any deletion -- test safety cannot rest on the code under test.
    sed -i "s#^    for dir in /tmp /dev/shm ; do#    for dir in $dir/roots/tmp $dir/roots/shm ; do#" "$dir/42-cleanup"

    # And the redirect is verified rather than assumed: a template edit that
    # changes that line would silently hand the real roots back.
    if ! grep -qF "for dir in $dir/roots/tmp $dir/roots/shm ; do" "$dir/42-cleanup"; then
        printf '  FAIL harness: cleanup roots were not redirected into the fixture\n' >&2
        return 1
    fi
    if grep -qE '^[[:space:]]*for dir in .*(^|[[:space:]])(/tmp|/dev/shm)([[:space:]]|$)' "$dir/42-cleanup"; then
        printf '  FAIL harness: a cleanup root still points outside the fixture\n' >&2
        return 1
    fi

    printf '%s' "$dir"
}

echo
echo "42-lastuserjob-cleanup -- deletion guards"

identity="$(cleanup_identity)" || identity=""
if [ -z "$identity" ]; then
    printf '  skip deletion guards (no resolvable unprivileged account)\n'
else

# Every guard case below puts a file the identity owns under a fixture root and
# asserts it is still there afterwards. Checking only that "Removed residual"
# was absent would pass for a script that deleted everything and failed to log.
guard_case() {
    # $1 = label, $2 = expected log fragment, $3 = SLURM_JOB_USER,
    # $4 = contents of localusers.backup
    local label="$1" expect="$2" user="$3" list="$4"
    local dir
    dir="$(setup_cleanup)" || { no "$label" "harness setup failed"; return; }

    if [ -n "$list" ]; then
        printf '%s\n' "$list" > "$dir/etc/slurm/localusers.backup"
    fi
    own_file "$identity" "$dir/roots/tmp/scratch" || { no "$label" "could not create the fixture file"; return; }

    local out rc
    out=$(PATH="$dir/bin:$PATH" SLURM_JOB_USER="$user" "$dir/42-cleanup" 2>&1); rc=$?

    if [ "$rc" != 0 ]; then
        no "$label" "rc=$rc out=$(printf '%s' "$out" | tail -1)"
    elif [ ! -e "$dir/roots/tmp/scratch" ]; then
        no "$label" "the guard did not stop the deletion: the fixture file is gone"
    elif [ -n "$expect" ] && ! grep -q "$expect" <<<"$out"; then
        no "$label" "expected the log to mention '$expect'; out=$(printf '%s' "$out" | tail -1)"
    else
        ok "$label"
    fi
}

# --- listed operator: files must survive -----------------------------------
# The identity resolves, so reaching the deletion is a live possibility here;
# only the exemption stops it.
guard_case "a user listed in localusers.backup is spared" "" "$identity" "$identity
bob"

# --- unreadable list: must not authorise deletion --------------------------
# No localusers.backup at all -> grep exits 2, which is "lookup failed", not
# "not listed".
guard_case "an unreadable localusers.backup stops the cleanup" \
    "localusers lookup failed" "$identity" ""

# --- unresolvable account: must not delete on a partial listing ------------
dir="$(setup_cleanup)" || dir=""
if [ -z "$dir" ]; then
    no "an unresolvable owner stops the cleanup" "harness setup failed"
else
    : > "$dir/etc/slurm/localusers.backup"
    own_file "$identity" "$dir/roots/tmp/scratch"
    out=$(PATH="$dir/bin:$PATH" SLURM_JOB_USER=nosuchuser-$$ "$dir/42-cleanup" 2>&1); rc=$?
    if [ "$rc" = 0 ] && [ -e "$dir/roots/tmp/scratch" ] \
       && grep -q 'cannot resolve' <<<"$out"; then
        ok "an unresolvable owner stops the cleanup"
    else
        no "an unresolvable owner stops the cleanup" "rc=$rc out=$(printf '%s' "$out" | tail -1)"
    fi
fi

# --- root and empty user are left alone ------------------------------------
dir="$(setup_cleanup)" || dir=""
if [ -z "$dir" ]; then
    no "root is left alone" "harness setup failed"
else
    : > "$dir/etc/slurm/localusers.backup"
    own_file "$identity" "$dir/roots/tmp/scratch"
    out=$(PATH="$dir/bin:$PATH" SLURM_JOB_USER=root "$dir/42-cleanup" 2>&1); rc=$?
    if [ "$rc" = 0 ] && [ -e "$dir/roots/tmp/scratch" ] \
       && ! grep -q 'Removed residual' <<<"$out"; then
        ok "root is left alone"
    else
        no "root is left alone" "rc=$rc out=$(printf '%s' "$out" | tail -1)"
    fi
fi

# --- the cleanup does run when nothing stops it ----------------------------
# Without this the four cases above would all pass for a script that never
# deletes anything, and the guards they pin would be proving nothing.
dir="$(setup_cleanup)" || dir=""
if [ -z "$dir" ]; then
    no "an unlisted, resolvable owner is cleaned up" "harness setup failed"
else
    : > "$dir/etc/slurm/localusers.backup"
    own_file "$identity" "$dir/roots/tmp/scratch"
    out=$(PATH="$dir/bin:$PATH" SLURM_JOB_USER="$identity" "$dir/42-cleanup" 2>&1); rc=$?
    if [ "$rc" = 0 ] && [ ! -e "$dir/roots/tmp/scratch" ]; then
        ok "an unlisted, resolvable owner is cleaned up"
    else
        no "an unlisted, resolvable owner is cleaned up" \
           "rc=$rc file exists=$([ -e "$dir/roots/tmp/scratch" ] && echo yes || echo no) out=$(printf '%s' "$out" | tail -1)"
    fi
fi

fi  # identity

# ---------------------------------------------------------------------------
# 42-lastuserjob-cleanup: the mount boundary
# ---------------------------------------------------------------------------
#
# -xdev bounds the walk. What deletes is `rm -fr`, and these cases are the ways
# a bounded walk still hands it a path that leads off this filesystem:
#
#   - a user-owned directory that holds a mount: the walk stops at the mount,
#     but the parent is selected and the recursive delete goes straight in;
#   - a user-owned mount root: selected in its own right;
#   - a bind mount from the same filesystem: not a boundary to -xdev at all.
#
# Each needs a real mount, so the block is skipped where the harness cannot
# make one.
mount_fixture_ready=no
if [ "$(id -u)" = 0 ] && [ -n "${identity:-}" ] && command -v mount >/dev/null 2>&1 \
   && [ -r /proc/self/mountinfo ]; then
    mount_fixture_ready=yes
fi

if [ "$mount_fixture_ready" != yes ]; then
    printf '  skip mount-boundary cases (needs root, an unprivileged account, mount and /proc/self/mountinfo)\n'
else
    mounted=()
    cleanup_mounts() {
        local i
        for (( i=${#mounted[@]}-1 ; i>=0 ; i-- )); do
            umount "${mounted[$i]}" 2>/dev/null
        done
        mounted=()
    }

    mount_case() {
        # $1 = label, $2 = fixture builder, $3 = assertion
        local label="$1" build="$2" assert="$3" dir
        dir="$(setup_cleanup)" || { no "$label" "harness setup failed"; return; }
        : > "$dir/etc/slurm/localusers.backup"

        if ! "$build" "$dir"; then
            printf '  skip %s (could not build the fixture)\n' "$label"
            cleanup_mounts
            return
        fi

        # The exit status is part of the result. A cleanup that exits before it
        # reaches the fixture leaves every payload where it was, and a surviving
        # payload is what these assertions look for, so an early exit would
        # read as a boundary that held.
        local rc
        PATH="$dir/bin:$PATH" SLURM_JOB_USER="$identity" "$dir/42-cleanup" >/dev/null 2>&1; rc=$?
        if [ "$rc" != 0 ]; then
            no "$label" "the cleanup exited $rc"
        elif "$assert" "$dir"; then
            ok "$label"
        else
            no "$label" "see $dir"
        fi
        cleanup_mounts
    }

    # --- the walk stays off a submount --------------------------------------
    build_submount() {
        local dir="$1"
        mkdir -p "$dir/roots/tmp/sub" || return 1
        mount -t tmpfs tmpfs "$dir/roots/tmp/sub" 2>/dev/null || return 1
        mounted+=( "$dir/roots/tmp/sub" )
        own_file "$identity" "$dir/roots/tmp/on-this-fs" || return 1
        own_file "$identity" "$dir/roots/tmp/sub/on-the-submount" || return 1
    }
    assert_submount() {
        local dir="$1"
        [ ! -e "$dir/roots/tmp/on-this-fs" ] && [ -e "$dir/roots/tmp/sub/on-the-submount" ]
    }
    mount_case "the walk stays off a submount" build_submount assert_submount

    # --- a user-owned parent holding a mount is not deleted recursively ------
    # This is the case -xdev alone does not cover: find stops at the mount, but
    # it still prints the parent, and `rm -fr` on the parent descends into the
    # mount the walk refused to enter.
    build_owned_parent() {
        local dir="$1"
        mkdir -p "$dir/roots/tmp/owned/sub" || return 1
        mount -t tmpfs tmpfs "$dir/roots/tmp/owned/sub" 2>/dev/null || return 1
        mounted+=( "$dir/roots/tmp/owned/sub" )
        chown "$identity" "$dir/roots/tmp/owned" || return 1
        own_file "$identity" "$dir/roots/tmp/owned/beside" || return 1
        own_file "$identity" "$dir/roots/tmp/owned/sub/on-the-mount" || return 1
    }
    assert_owned_parent() {
        local dir="$1"
        # The mount survives, and cleanup still happened around it.
        [ -e "$dir/roots/tmp/owned/sub/on-the-mount" ] \
            && [ ! -e "$dir/roots/tmp/owned/beside" ]
    }
    mount_case "a user-owned parent holding a mount keeps it" \
        build_owned_parent assert_owned_parent

    # --- a user-owned mount root is not emptied -----------------------------
    build_owned_mount_root() {
        local dir="$1"
        mkdir -p "$dir/roots/tmp/scratchmnt" || return 1
        mount -t tmpfs tmpfs "$dir/roots/tmp/scratchmnt" 2>/dev/null || return 1
        mounted+=( "$dir/roots/tmp/scratchmnt" )
        # The mount root itself belongs to the user, so the walk selects it.
        chown "$identity" "$dir/roots/tmp/scratchmnt" || return 1
        own_file "$identity" "$dir/roots/tmp/scratchmnt/payload" || return 1
        # Something outside the mount for the cleanup to remove. A surviving
        # payload on its own is also what a cleanup that never ran leaves
        # behind.
        own_file "$identity" "$dir/roots/tmp/beside" || return 1
    }
    assert_owned_mount_root() {
        local dir="$1"
        [ -e "$dir/roots/tmp/scratchmnt/payload" ] && [ ! -e "$dir/roots/tmp/beside" ]
    }
    mount_case "a user-owned mount root is left alone" \
        build_owned_mount_root assert_owned_mount_root

    # --- a same-filesystem bind mount is a boundary too ---------------------
    # -xdev compares device numbers, and a bind mount of this same filesystem
    # has the same one, so the walk goes in and deletes the real data.
    build_same_device_bind() {
        local dir="$1"
        mkdir -p "$dir/roots/real" "$dir/roots/tmp/owned/data" || return 1
        own_file "$identity" "$dir/roots/real/keep-me" || return 1
        mount --bind "$dir/roots/real" "$dir/roots/tmp/owned/data" 2>/dev/null || return 1
        mounted+=( "$dir/roots/tmp/owned/data" )
        chown "$identity" "$dir/roots/tmp/owned" || return 1
        own_file "$identity" "$dir/roots/tmp/owned/beside" || return 1
    }
    assert_same_device_bind() {
        local dir="$1"
        [ -e "$dir/roots/real/keep-me" ] && [ ! -e "$dir/roots/tmp/owned/beside" ]
    }
    mount_case "a same-filesystem bind mount is left alone" \
        build_same_device_bind assert_same_device_bind
fi

# ---------------------------------------------------------------------------
# 42-lastuserjob-cleanup: mount points that read as patterns
# ---------------------------------------------------------------------------
#
# Each mount point becomes a `find -path` pattern, and -path takes a glob, not
# a literal; quoting does not change that. Left unescaped:
#
#   - `data[1]` matches a sibling called `data1` and not itself, and a backslash
#     is the glob escape character, so `data\1` does the same. The mount goes
#     unpruned and its root reaches `rm -fr`, while the sibling is spared;
#   - `*` and `?` still match themselves, so the mount survives and only the
#     spared sibling gives the missing escape away.
#
# A mount point ending in a newline is the other way to lose the name: decoding
# it through a command substitution strips the newline, and the pattern then
# names the sibling without it.
#
# A mount point containing whitespace is lost a third way: mountinfo writes a
# space as `\040` and a tab as `\011`, so a script that skips the decode
# prunes a sibling literally called `data\0401` and walks into `data 1`, and
# one that splits the field on whitespace prunes `data` instead.
#
# So every case pairs the mount with the sibling its mishandled name would
# match and asserts both halves: the payload under the mount survives, and the
# sibling is cleaned up.
#
# The mount-boundary cases need root because they need a real mount. These do
# not: what is under test is how the script treats a name it read from the mount
# table, so the table is scripted instead. An `awk` stub on PATH answers the
# script's one awk call with the mount point the case names, written the way the
# kernel writes field 5 of mountinfo. The script still insists on a readable
# /proc/self/mountinfo before it asks.
echo
echo "42-lastuserjob-cleanup -- mount points that read as patterns"

if [ -z "${identity:-}" ] || [ ! -r /proc/self/mountinfo ]; then
    printf '  skip mount-pathname cases (needs a resolvable unprivileged account and /proc/self/mountinfo)\n'
else

# Backslash, space, tab and newline are written as octal escapes; everything
# else, glob characters included, is written as it is. Nothing here goes
# through a command substitution, which would strip the trailing newline one of
# the cases depends on.
mountinfo_escape() {
    local path="$1"
    path=${path//\\/\\134}
    path=${path// /\\040}
    path=${path//$'\t'/\\011}
    path=${path//$'\n'/\\012}
    printf '%s\n' "$path"
}

pathname_case() {
    # $1 = label, $2 = name of the mount point, $3 = name of the sibling the
    # mount point would match if it were taken as a pattern
    local label="$1" mount_name="$2" sibling_name="$3" dir
    dir="$(setup_cleanup)" || { no "$label" "harness setup failed"; return; }
    : > "$dir/etc/slurm/localusers.backup"

    local mount_point="$dir/roots/tmp/$mount_name"
    local sibling="$dir/roots/tmp/$sibling_name"
    if ! { own_dir "$identity" "$mount_point" \
            && own_file "$identity" "$mount_point/payload" \
            && own_file "$identity" "$sibling"; }; then
        no "$label" "could not create the fixture"
        return
    fi

    mountinfo_escape "$mount_point" > "$dir/mount-points"
    cat > "$dir/bin/awk" <<STUB
#!/usr/bin/env bash
cat "$dir/mount-points"
STUB
    chmod +x "$dir/bin/awk"

    local out rc
    out=$(PATH="$dir/bin:$PATH" SLURM_JOB_USER="$identity" "$dir/42-cleanup" 2>&1); rc=$?

    if [ "$rc" != 0 ]; then
        no "$label" "rc=$rc out=$(printf '%s' "$out" | tail -1)"
    elif [ ! -e "$mount_point/payload" ]; then
        no "$label" "the mount point was not pruned: the payload under it is gone"
    elif [ -e "$sibling" ]; then
        no "$label" "the sibling $sibling_name was spared"
    else
        ok "$label"
    fi
}

pathname_case "a mount point containing [ is matched literally"           'data[1]'   'data1'
pathname_case "a mount point containing a backslash is matched literally" 'data\1'    'data1'
pathname_case "a mount point containing * is matched literally"           'data*1'    'datax1'
pathname_case "a mount point containing ? is matched literally"           'data?1'    'datax1'
pathname_case "a mount point ending in a newline keeps it"                $'data1\n'  'data1'
# The undecoded \040 names a sibling with a literal backslash in it.
pathname_case "a mount point containing a space is decoded"               'data 1'    'data\0401'
# And a field split on whitespace names the first word.
pathname_case "a mount point containing a space is not split"             'data 1'    'data'
pathname_case "a mount point containing a tab is decoded"                 $'data\t1'  'data\0111'

fi  # identity and /proc/self/mountinfo

# ---------------------------------------------------------------------------
# Job scope: dispatcher guard, script 40, script 42 and script 50
# ---------------------------------------------------------------------------
#
# Everything here runs against a fake /proc (fake_proc above) and a recording
# `kill`, so it needs neither Slurm, cgroups nor root. The ending job is 1000
# and the user is uid 1001 throughout; "squeue says clear" is the stub with no
# jobs.

# Fake process tables for the dispatcher cases.
proc_other_job_v1() { fake_proc "$1" 2001 1001 "$(cg_v1_job 1001 1002)"; }
proc_other_job_v2() { fake_proc "$1" 2001 1001 "$(cg_v2_job 1002)"; }
proc_other_job_sluid() { fake_proc "$1" 2001 1001 "$(cg_v2_sluid s5K1KKYAYG5D00)"; }
proc_own_job_only() { fake_proc "$1" 2001 1001 "$(cg_v2_job 1000)"; }
proc_waiting_area() { fake_proc "$1" 2001 1001 '0::/system.slice/slurmstepd.scope/system'; }
proc_other_job_other_user() { fake_proc "$1" 2001 1555 "$(cg_v2_job 1002)"; }
proc_unrecognised() { fake_proc "$1" 2001 1001 'this is not a cgroup file'; }
proc_unreadable() { fake_proc "$1" 2001 1001 ''; }
proc_orphan_only() { fake_proc "$1" 2001 1001 "$(cg_none 1001)"; }
proc_own_and_other() { fake_proc "$1" 2001 1001 "$(cg_v2_job 1000)"; fake_proc "$1" 2002 1001 "$(cg_v2_job 1003)"; }

echo
echo "run-parts.sh -- job-scoped guard (scope=job)"
RENDER_SCOPE=job
runparts_case "node scan: another job's process (cgroup v1) blocks though squeue is clear" skipped "" 0 proc_other_job_v1
runparts_case "node scan: another job's process (cgroup v2) blocks though squeue is clear" skipped "" 0 proc_other_job_v2
runparts_case "node scan: another job's process (cgroup v2, SLUID) blocks though squeue is clear" skipped "" 0 proc_other_job_sluid
# Slurm kills the ending job's processes before the epilog: one still in a job
# cgroup is one it could not kill, and the cleanup waits for the next epilog.
runparts_case "node scan: a process left in any job cgroup blocks"                         skipped "" 0 proc_own_job_only
runparts_case "node scan: a process in slurmstepd's waiting area fails closed"             skipped "" 0 proc_waiting_area
runparts_case "node scan: another user's job does not block"                               ran     "" 0 proc_other_job_other_user
runparts_case "node scan: an orphan alone does not block"                                  ran     "" 0 proc_orphan_only
runparts_case "node scan: own and other job together block"                                skipped "" 0 proc_own_and_other
runparts_case "node scan: an unrecognised cgroup layout fails closed"                      skipped "" 0 proc_unrecognised
runparts_case "node scan: an empty cgroup file fails closed"                               skipped "" 0 proc_unreadable
runparts_case "squeue still wins when the node scan is clear"                              skipped "1001:running" 0 proc_orphan_only

# No process table at all: a /proc with nothing in it must read as "cannot
# tell", not as "no other job".
proc_empty() { rm -rf "$1"; mkdir -p "$1"; }
runparts_case "node scan: an empty process table fails closed" skipped "" 0 proc_empty

# A dispatcher that cannot load its helper library must defer, not run on
# squeue alone.
dir="$(setup_runparts)" || dir=""
if [ -z "$dir" ]; then
    no "a missing helper library fails closed" "harness setup failed"
else
    rm -f "$dir/lib/epilog-job-scope.sh" "$dir/RAN"
    out=$(PATH="$dir/bin:$PATH" SLURM_JOB_USER=alice SLURM_JOB_UID=1001 SLURM_JOB_ID=1000 \
          HOSTNAME=node1 SLURM_JOBID=1000 SQUEUE_JOBS="" SQUEUE_RC=0 \
          "$dir/run-parts.sh" "$dir/parts" 2>&1); rc=$?
    if [ "$rc" = 0 ] && [ ! -e "$dir/RAN" ] && grep -q 'cannot load' <<<"$out"; then
        ok "a missing helper library fails closed"
    else
        no "a missing helper library fails closed" "rc=$rc ran=$([ -e "$dir/RAN" ] && echo yes || echo no) out=$(printf '%s' "$out" | tail -1)"
    fi
fi

# A job without SLURM_JOB_UID resolves the account instead; with neither it
# defers.
dir="$(setup_runparts)" || dir=""
if [ -z "$dir" ]; then
    no "no resolvable uid fails closed" "harness setup failed"
else
    rm -f "$dir/RAN"
    out=$(PATH="$dir/bin:$PATH" SLURM_JOB_USER="nosuchuser-$$" SLURM_JOB_UID="" SLURM_JOB_ID=1000 \
          HOSTNAME=node1 SLURM_JOBID=1000 SQUEUE_JOBS="" SQUEUE_RC=0 \
          "$dir/run-parts.sh" "$dir/parts" 2>&1); rc=$?
    if [ "$rc" = 0 ] && [ ! -e "$dir/RAN" ] && grep -q 'no numeric uid' <<<"$out"; then
        ok "no resolvable uid fails closed"
    else
        no "no resolvable uid fails closed" "rc=$rc out=$(printf '%s' "$out" | tail -1)"
    fi
fi

echo
echo "run-parts.sh -- scope=user ignores the node scan"
RENDER_SCOPE=user
runparts_case "user scope: another job's cgroup does not block (squeue only)" ran "" 0 proc_other_job_v2
RENDER_SCOPE=job

# --- the cgroup spec ------------------------------------------------------------
# tests/lib/slurm_cgroups.json is the one spec of whose a process is by its
# /proc/<pid>/cgroup; cluster_tools.job_cgroup is tested on the same cases.
echo
echo "shared/lib/epilog-job-scope.sh -- every case of tests/lib/slurm_cgroups.json"
spec="$work/spec"
rm -rf "$spec"; mkdir -p "$spec/cases"
if render "shared/lib/epilog-job-scope.sh" "$spec/lib.sh" \
   && python3 - "$repo/tests/lib/slurm_cgroups.json" "$spec/cases" <<'PY'
import json, pathlib, sys
out = pathlib.Path(sys.argv[2])
for i, case in enumerate(json.loads(pathlib.Path(sys.argv[1]).read_text())["cases"]):
    (out / f"{i:03d}.cgroup").write_text(case["cgroup"])
    (out / f"{i:03d}.want").write_text(" ".join([case["kind"], *case["keys"]]))
    (out / f"{i:03d}.name").write_text(case["name"])
PY
then
    cases=0
    for f in "$spec"/cases/*.cgroup; do
        [ -e "$f" ] || continue
        cases=$((cases + 1))
        base="${f%.cgroup}"
        got=$(epilog_proc_root=/nonexistent bash -c '. "$1" && epilog_cgroup_classify <"$2"' _ "$spec/lib.sh" "$f" 2>&1)
        want=$(<"$base.want")
        if [ "$got" = "$want" ]; then
            ok "spec: $(<"$base.name")"
        else
            no "spec: $(<"$base.name")" "got '$got', wanted '$want'"
        fi
    done
    [ "$cases" -gt 0 ] || no "spec: the cases are read" "no case in tests/lib/slurm_cgroups.json"
else
    no "spec: the cases are read" "harness setup failed"
fi

# --- 40-lastuserjob-processes ------------------------------------------------
echo
echo "40-lastuserjob-processes -- PID classification (scope=job)"

setup_procs() {
    local dir="$work/procs"
    rm -rf "$dir"
    mkdir -p "$dir/proc" "$dir/lib" "$dir/etc"
    write_logger_stub "$dir"
    render "epilog.d/40-lastuserjob-processes" "$dir/40-processes" || return 1
    render "shared/lib/epilog-job-scope.sh" "$dir/lib/epilog-job-scope.sh" || return 1
    use_localusers "$dir/40-processes" "$dir/etc/localusers.backup" || return 1
    sed -i "s#^epilog_proc_root=.*#epilog_proc_root='$dir/proc'#" "$dir/40-processes"
    sed -i "s#^job_scope_lib=.*#job_scope_lib='$dir/lib/epilog-job-scope.sh'#" "$dir/40-processes"
    # A recording kill: the harness must never signal a real process.
    cat > "$dir/bin/kill" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$dir/kill.log"
STUB
    chmod +x "$dir/bin/kill"
    sed -i "s#^kill_bin=.*#kill_bin='$dir/bin/kill'#" "$dir/40-processes"
    for want in "epilog_proc_root='$dir/proc'" \
                "job_scope_lib='$dir/lib/epilog-job-scope.sh'" "kill_bin='$dir/bin/kill'"; do
        if ! grep -qF "$want" "$dir/40-processes"; then
            printf '  FAIL harness: 40 was not redirected into the fixture (%s)\n' "$want" >&2
            return 1
        fi
    done
    : > "$dir/etc/localusers.backup"
    : > "$dir/kill.log"
    printf '%s' "$dir"
}

# $1 label, $2 builder, $3 "pid pid ..." expected killed, $4 EPILOG_LAST_USER_JOB
procs_case() {
    local label="$1" build="$2" want="$3" last="${4-0}" dir
    dir="$(setup_procs)" || { no "$label" "harness setup failed"; return; }
    "$build" "$dir/proc"
    local out rc
    out=$(PATH="$dir/bin:$PATH" SLURM_JOB_USER=alice SLURM_JOB_UID=1001 SLURM_JOB_ID=1000 \
          EPILOG_LAST_USER_JOB="$last" "$dir/40-processes" 2>&1); rc=$?
    local got
    got=$(sed -e 's/^-9 -- //' "$dir/kill.log" | sort -n | tr '\n' ' ' | sed 's/ $//')
    if [ "$rc" != 0 ]; then
        no "$label" "rc=$rc out=$(printf '%s' "$out" | tail -1)"
    elif [ "$got" != "$want" ]; then
        no "$label" "killed '$got', wanted '$want'"
    else
        ok "$label"
    fi
}

# One PID per row of the classification table in 40's header, on each layout.
# 3001 sits in the ending job's cgroup (one Slurm could not kill), 3002 in
# another job's, 3003 is an orphan, 3004 unparsable; 3005 belongs to someone
# else's job and is never ours to touch. Only the orphan is ever killed.
mixed_v1() { fake_proc "$1" 3001 1001 "$(cg_v1_job 1001 1000)"; fake_proc "$1" 3002 1001 "$(cg_v1_job 1001 1002)"
             fake_proc "$1" 3003 1001 "$(cg_none 1001)"; fake_proc "$1" 3004 1001 'garbage'
             fake_proc "$1" 3005 1777 "$(cg_v1_job 1777 1050)"; }
mixed_v2() { fake_proc "$1" 3001 1001 "$(cg_v2_job 1000)"; fake_proc "$1" 3002 1001 "$(cg_v2_job 1002)"
             fake_proc "$1" 3003 1001 "$(cg_none 1001)"; fake_proc "$1" 3004 1001 'garbage'
             fake_proc "$1" 3005 1777 "$(cg_v2_job 1050)"; }
mixed_sluid() { fake_proc "$1" 3001 1001 "$(cg_v2_sluid s5K1KKYAYG5D00)"; fake_proc "$1" 3002 1001 "$(cg_v2_sluid sEKNKTV3WPV500)"
                fake_proc "$1" 3003 1001 "$(cg_none 1001)"; fake_proc "$1" 3004 1001 'garbage'
                fake_proc "$1" 3005 1777 "$(cg_v2_sluid s0000000000000)"; }

for layout in v1 v2 sluid; do
    procs_case "$layout: every process in a job cgroup is spared, the orphan too while another job may exist" "mixed_$layout" "" 0
    procs_case "$layout: the orphan is killed when the user has no other job, nothing in a job cgroup" "mixed_$layout" "3003" 1
done

exited_between() { fake_proc "$1" 3010 1001 "$(cg_v2_job 1000)"; rm -f "$1/3010/cgroup"; }
procs_case "a process that exits between listing and classifying is skipped" exited_between "" 1

# A vanished process is routine and must not put bash errors in the epilog log.
vanished_quiet_dir="$(setup_procs)" || vanished_quiet_dir=""
if [ -z "$vanished_quiet_dir" ]; then
    no "a vanished process produces no shell error output" "harness setup failed"
else
    fake_proc "$vanished_quiet_dir/proc" 3012 1001 "$(cg_v2_job 1000)"
    rm -f "$vanished_quiet_dir/proc/3012/cgroup"
    out=$(PATH="$vanished_quiet_dir/bin:$PATH" SLURM_JOB_USER=alice SLURM_JOB_UID=1001 SLURM_JOB_ID=1000 \
          EPILOG_LAST_USER_JOB=1 "$vanished_quiet_dir/40-processes" 2>&1)
    if ! grep -qi 'no such file\|line [0-9]*:' <<<"$out"; then
        ok "a vanished process produces no shell error output"
    else
        no "a vanished process produces no shell error output" "$(printf '%s' "$out" | head -2 | tr '\n' ' ')"
    fi
fi

unparsable_only() { fake_proc "$1" 3011 1001 'x:y'; }
procs_case "an unparsable cgroup line is skipped, not killed" unparsable_only "" 1

# A process in Slurm's hierarchy but in no job is not an orphan: left alone.
waiting_area() { fake_proc "$1" 3020 1001 '0::/system.slice/slurmstepd.scope/system'; }
procs_case "a process in slurmstepd's waiting area is left alone" waiting_area "" 1

# A user's own cgroup named like a job is no job: an orphan.
lookalike() { fake_proc "$1" 3021 1001 '0::/user.slice/user-1001.slice/user@1001.service/app.slice/job_5'; }
procs_case "a user's cgroup named job_5 is an orphan" lookalike "3021" 1

# --- exemptions and edge users -------------------------------------------------
exempt_dir="$(setup_procs)" || exempt_dir=""
if [ -z "$exempt_dir" ]; then
    no "a user in localusers.backup is spared" "harness setup failed"
else
    mixed_v2 "$exempt_dir/proc"
    printf 'alice\n' > "$exempt_dir/etc/localusers.backup"
    PATH="$exempt_dir/bin:$PATH" SLURM_JOB_USER=alice SLURM_JOB_UID=1001 SLURM_JOB_ID=1000 \
        EPILOG_LAST_USER_JOB=1 "$exempt_dir/40-processes" >/dev/null 2>&1; rc=$?
    if [ "$rc" = 0 ] && [ ! -s "$exempt_dir/kill.log" ]; then
        ok "a user in localusers.backup is spared"
    else
        no "a user in localusers.backup is spared" "rc=$rc kills=$(tr '\n' ' ' < "$exempt_dir/kill.log")"
    fi
fi

root_dir="$(setup_procs)" || root_dir=""
if [ -z "$root_dir" ]; then
    no "root is never touched" "harness setup failed"
else
    fake_proc "$root_dir/proc" 3030 0 "$(cg_v2_job 1000)"
    PATH="$root_dir/bin:$PATH" SLURM_JOB_USER=root SLURM_JOB_UID=0 SLURM_JOB_ID=1000 \
        EPILOG_LAST_USER_JOB=1 "$root_dir/40-processes" >/dev/null 2>&1; rc=$?
    if [ "$rc" = 0 ] && [ ! -s "$root_dir/kill.log" ]; then
        ok "root is never touched"
    else
        no "root is never touched" "rc=$rc kills=$(tr '\n' ' ' < "$root_dir/kill.log")"
    fi
fi

lookup_dir="$(setup_procs)" || lookup_dir=""
if [ -z "$lookup_dir" ]; then
    no "an unreadable localusers.backup stops the process cleanup" "harness setup failed"
else
    mixed_v2 "$lookup_dir/proc"
    rm -f "$lookup_dir/etc/localusers.backup"
    out=$(PATH="$lookup_dir/bin:$PATH" SLURM_JOB_USER=alice SLURM_JOB_UID=1001 SLURM_JOB_ID=1000 \
          EPILOG_LAST_USER_JOB=1 "$lookup_dir/40-processes" 2>&1); rc=$?
    if [ "$rc" = 0 ] && [ ! -s "$lookup_dir/kill.log" ] && grep -q 'localusers lookup failed' <<<"$out"; then
        ok "an unreadable localusers.backup stops the process cleanup"
    else
        no "an unreadable localusers.backup stops the process cleanup" "rc=$rc out=$(printf '%s' "$out" | tail -1)"
    fi
fi

# Without the dispatcher's word that this was the last job, an orphan stays.
standalone_dir="$(setup_procs)" || standalone_dir=""
if [ -z "$standalone_dir" ]; then
    no "run without the dispatcher, orphans are spared" "harness setup failed"
else
    mixed_v2 "$standalone_dir/proc"
    PATH="$standalone_dir/bin:$PATH" SLURM_JOB_USER=alice SLURM_JOB_UID=1001 SLURM_JOB_ID=1000 \
        "$standalone_dir/40-processes" >/dev/null 2>&1; rc=$?
    got=$(sed -e 's/^-9 -- //' "$standalone_dir/kill.log" | tr '\n' ' ' | sed 's/ $//')
    if [ "$rc" = 0 ] && [ -z "$got" ]; then
        ok "run without the dispatcher, orphans are spared"
    else
        no "run without the dispatcher, orphans are spared" "rc=$rc killed='$got'"
    fi
fi

# And in scope=user the original killall is what runs.
user40="$work/user40"; rm -rf "$user40"; mkdir -p "$user40/bin" "$user40/etc"
write_logger_stub "$user40"
if RENDER_SCOPE=user render "epilog.d/40-lastuserjob-processes" "$user40/40" \
   && grep -q 'killall -9 -u' "$user40/40" && ! grep -q 'epilog-job-scope' "$user40/40"; then
    ok "user scope renders the original killall script"
else
    no "user scope renders the original killall script" "see $user40"
fi

# --- 42-lastuserjob-cleanup: per-job private directories ---------------------------
echo
echo "42-lastuserjob-cleanup -- per-job private directories (scope=job)"
jc_identity="$(cleanup_identity)" || jc_identity=""
if [ -z "$jc_identity" ]; then
    printf '  skip private-directory cases (no resolvable unprivileged account)\n'
else
    jc_case() {
        # $1 = label, $2 = slurm_job_private_dirs, $3 = expect gone|kept
        local label="$1" expect="$3" dir
        dir="$(RENDER_PRIVATE_DIRS="$2" setup_cleanup)" || { no "$label" "harness setup failed"; return; }
        : > "$dir/etc/slurm/localusers.backup"
        own_file "$jc_identity" "$dir/roots/tmp/scratch" || { no "$label" "could not create the fixture file"; return; }
        local out rc state=kept
        out=$(PATH="$dir/bin:$PATH" SLURM_JOB_USER="$jc_identity" "$dir/42-cleanup" 2>&1); rc=$?
        [ -e "$dir/roots/tmp/scratch" ] || state=gone
        if [ "$rc" != 0 ]; then
            no "$label" "rc=$rc out=$(printf '%s' "$out" | tail -1)"
        elif [ "$state" != "$expect" ]; then
            no "$label" "file is $state, wanted $expect"
        elif [ "$expect" = kept ] && ! grep -q 'removed with the job' <<<"$out"; then
            no "$label" "skipped without logging why: $(printf '%s' "$out" | tail -1)"
        else
            ok "$label"
        fi
    }
    jc_case "private /tmp and /dev/shm: file cleanup is skipped and logged" "/tmp,/var/tmp,/dev/shm" kept
    jc_case "only /tmp private: the host's /dev/shm still needs the cleanup" "/tmp" gone
    jc_case "no private directories: cleanup runs" "" gone
fi

# --- 43-lastuserjob-ipc -----------------------------------------------------------
# The user's System V IPC objects and POSIX message queues go with their last job, an
# operator's stay, and nothing fails. The kernel's tables, ipcrm and /dev/mqueue are
# fixtures: the suite never removes a real IPC object of whoever runs it.
echo
echo "43-lastuserjob-ipc -- the user's IPC objects go, nobody else's"
ipc_setup() {
    # $1 = fixture dir; the job user is uid 1001
    local dir="$1"
    rm -rf "$dir"; mkdir -p "$dir/sysvipc" "$dir/mqueue" "$dir/bin"
    write_logger_stub "$dir"
    printf '#!/bin/sh\necho "$*" >> "%s/ipcrm.log"\n' "$dir" > "$dir/bin/ipcrm"
    chmod +x "$dir/bin/ipcrm"
    printf '%s\n' "key shmid perms size cpid lpid nattch uid gid" \
        "0 11 600 4096 1 1 0 1001 1001" "0 12 600 4096 1 1 0 1002 1002" > "$dir/sysvipc/shm"
    printf '%s\n' "key semid perms nsems uid gid" "0 21 600 1 1001 1001" > "$dir/sysvipc/sem"
    printf '%s\n' "key msqid perms cbytes qnum lspid lrpid uid gid" "0 31 600 0 0 0 0 1002 1002" > "$dir/sysvipc/msg"
    : > "$dir/mqueue/q-mine"
    render "epilog.d/43-lastuserjob-ipc" "$dir/43" || return 1
    use_localusers "$dir/43" "$dir/localusers.backup" || return 1
    sed -i -e "s#^sysvipc=.*#sysvipc='$dir/sysvipc'#" -e "s#^mqueue=.*#mqueue='$dir/mqueue'#" "$dir/43"
    grep -qF "sysvipc='$dir/sysvipc'" "$dir/43" && grep -qF "mqueue='$dir/mqueue'" "$dir/43"
}
ipc="$work/ipc"
if ipc_setup "$ipc"; then
    : > "$ipc/localusers.backup"
    me=$(id -u)
    # The mqueue fixture belongs to whoever runs the suite: run as that uid's job.
    sed -i "s/ 1001 / $me /" "$ipc/sysvipc/shm" "$ipc/sysvipc/sem"
    out=$(PATH="$ipc/bin:$PATH" SLURM_JOB_USER=u SLURM_JOB_UID="$me" bash "$ipc/43" 2>&1); rc=$?
    if [ "$rc" = 0 ] && [ "$(tr '\n' ' ' < "$ipc/ipcrm.log")" = "-m 11 -s 21 " ] \
       && [ ! -e "$ipc/mqueue/q-mine" ] && grep -q 'removed 3 IPC objects' <<<"$out"; then
        ok "the user's shared memory, semaphores and message queues go, another user's stay"
    else
        no "the user's shared memory, semaphores and message queues go, another user's stay" \
           "rc=$rc ipcrm=$(tr '\n' ' ' < "$ipc/ipcrm.log" 2>/dev/null) out=$(printf '%s' "$out" | tail -1)"
    fi
else
    no "43-lastuserjob-ipc renders" "harness setup failed"
fi
for listed in yes unreadable; do
    if ipc_setup "$ipc"; then
        if [ "$listed" = yes ]; then printf 'u\n' > "$ipc/localusers.backup"; fi
        out=$(PATH="$ipc/bin:$PATH" SLURM_JOB_USER=u SLURM_JOB_UID=1001 bash "$ipc/43" 2>&1); rc=$?
        if [ "$rc" = 0 ] && [ ! -e "$ipc/ipcrm.log" ] && [ -e "$ipc/mqueue/q-mine" ]; then
            ok "an operator's IPC objects stay (localusers.backup $listed)"
        else
            no "an operator's IPC objects stay (localusers.backup $listed)" "rc=$rc out=$(printf '%s' "$out" | tail -1)"
        fi
    else
        no "43-lastuserjob-ipc renders" "harness setup failed"
    fi
done
for uid in 0 '' x1; do
    if ipc_setup "$ipc"; then
        : > "$ipc/localusers.backup"
        PATH="$ipc/bin:$PATH" SLURM_JOB_USER=u SLURM_JOB_UID="$uid" bash "$ipc/43" >/dev/null 2>&1; rc=$?
        if [ "$rc" = 0 ] && [ ! -e "$ipc/ipcrm.log" ]; then
            ok "no usable uid ('$uid'): nothing is removed"
        else
            no "no usable uid ('$uid'): nothing is removed" "rc=$rc"
        fi
    fi
done

# --- 50-lastuserjob-all-enroot-dirs -----------------------------------------------
# The enroot script has no guard of its own; it is protected by the dispatcher.
# The dispatcher cases above run a -lastuserjob- marker, which is the same gate
# 50 sits behind, so what is pinned here is the rendering: the paths survive
# in both scopes, and nothing in the script changes with the scope.
echo
echo "50-lastuserjob-all-enroot-dirs -- same removal in both scopes, behind the stronger guard"
e50j="$work/e50j"; e50u="$work/e50u"; rm -rf "$e50j" "$e50u"; mkdir -p "$e50j" "$e50u"
if RENDER_SCOPE=job render "epilog.d/50-lastuserjob-all-enroot-dirs" "$e50j/50" \
   && RENDER_SCOPE=user render "epilog.d/50-lastuserjob-all-enroot-dirs" "$e50u/50" \
   && cmp -s "$e50j/50" "$e50u/50"; then
    ok "script 50 is identical in both scopes; the dispatcher guard is what changes"
else
    no "script 50 is identical in both scopes; the dispatcher guard is what changes" "rendered output differs"
fi
# The dispatcher test that matters for 50: a -lastuserjob-all-enroot-dirs
# script is skipped when the node scan finds another job.
dir="$(setup_runparts)" || dir=""
if [ -z "$dir" ]; then
    no "enroot cleanup is skipped when another job is present" "harness setup failed"
else
    rm -f "$dir/parts/"*
    n=50-lastuserjob-all-enroot-dirs
    printf '#!/usr/bin/env bash\ntouch "%s/RAN-50"\n' "$dir" > "$dir/parts/$n"; chmod +x "$dir/parts/$n"
    proc_other_job_v2 "$dir/proc"
    PATH="$dir/bin:$PATH" SLURM_JOB_USER=alice SLURM_JOB_UID=1001 SLURM_JOB_ID=1000 HOSTNAME=node1 \
        SLURM_JOBID=1000 SQUEUE_JOBS="" SQUEUE_RC=0 "$dir/run-parts.sh" "$dir/parts" >/dev/null 2>&1
    skipped_ok=1; [ ! -e "$dir/RAN-50" ] || skipped_ok=0
    rm -f "$dir/proc/2001/cgroup"; cg_none 1001 > "$dir/proc/2001/cgroup"
    PATH="$dir/bin:$PATH" SLURM_JOB_USER=alice SLURM_JOB_UID=1001 SLURM_JOB_ID=1000 HOSTNAME=node1 \
        SLURM_JOBID=1000 SQUEUE_JOBS="" SQUEUE_RC=0 "$dir/run-parts.sh" "$dir/parts" >/dev/null 2>&1
    if [ "$skipped_ok" = 1 ] && [ -e "$dir/RAN-50" ]; then
        ok "enroot cleanup is skipped when another job is present and runs when last"
    else
        no "enroot cleanup is skipped when another job is present and runs when last" \
           "skipped_when_other=$skipped_ok ran_when_last=$([ -e "$dir/RAN-50" ] && echo yes || echo no)"
    fi
fi

# --- node-wide (exclusive) parts ----------------------------------------------------
# The *-exclusive-* parts act on the whole node, so they run only for a job Slurm gave
# the whole node: its Exclusive field is NODE. OverSubscribe (%h) reads NO for every job
# on an OverSubscribe=NO partition, shared or not (2026-11-23: every ending job's epilog
# killed the other jobs' GPU processes).
echo
echo "run-parts.sh -- node-wide parts run only for a job with Exclusive=NODE"
dir="$(setup_runparts)" || dir=""
if [ -z "$dir" ]; then
    no "node-wide parts follow Slurm's Exclusive field" "harness setup failed"
else
    rm -f "$dir/parts/"*
    printf '#!/usr/bin/env bash\ntouch "%s/RAN-X"\n' "$dir" > "$dir/parts/50-exclusive-x"; chmod +x "$dir/parts/50-exclusive-x"
    runx() { rm -f "$dir/RAN-X"; PATH="$dir/bin:$PATH" SLURM_JOB_USER=alice SLURM_JOB_UID=1001 SLURM_JOB_ID=1000 SLURM_JOBID=1000 \
             SLURMD_NODENAME=nodeA HOSTNAME=host-a SQUEUE_JOBS="" SQUEUE_RC=0 "$@" "$dir/run-parts.sh" "$dir/parts" >/dev/null 2>&1
             [ -e "$dir/RAN-X" ] && echo ran || echo skipped; }
    node=$(runx env SQUEUE_EXCLUSIVE=NODE)
    shared=$(runx env SQUEUE_EXCLUSIVE=NO)
    user=$(runx env SQUEUE_EXCLUSIVE=USER)
    failed=$(runx env SQUEUE_EXCLUSIVE=NODE SQUEUE_EXCLUSIVE_RC=1)
    if [ "$node/$shared/$user/$failed" = "ran/skipped/skipped/skipped" ]; then
        ok "node-wide parts: Exclusive=NODE ran; NO, USER and a failed squeue skip"
    else
        no "node-wide parts follow Slurm's Exclusive field" "NODE=$node NO=$shared USER=$user failed=$failed"
    fi
    # Each context asks for itself: the epilog too, and a failed squeue there skips.
    epi=$(runx env SLURM_SCRIPT_CONTEXT=epilog_slurmd SQUEUE_EXCLUSIVE=NODE)
    epifail=$(runx env SLURM_SCRIPT_CONTEXT=epilog_slurmd SQUEUE_EXCLUSIVE=NODE SQUEUE_EXCLUSIVE_RC=1)
    if [ "$epi/$epifail" = "ran/skipped" ] && ! grep -q 'exclusive_record' "$dir/run-parts.sh"; then
        ok "the epilog asks Slurm itself; no record of a prolog's change is kept"
    else
        no "the epilog asks Slurm itself; no record of a prolog's change is kept" "epilog=$epi failed-squeue=$epifail"
    fi
    if grep -q 'squeue_timeout' "$dir/run-parts.sh" && grep -q -- '-O Exclusive' "$dir/run-parts.sh" \
       && ! grep -q '"%h"' "$dir/run-parts.sh"; then
        ok "squeue is bounded, the Exclusive field is read, and OverSubscribe is not"
    else
        no "squeue is bounded, the Exclusive field is read, and OverSubscribe is not" "see run-parts.sh"
    fi
fi

# --- enroot paths from enroot.conf ------------------------------------------------
# The prolog and epilog take enroot's per-user paths from enroot.conf at job time,
# the file the enroot role writes, so they cannot disagree with it. Templated from
# the enroot role's variables they rendered empty wherever that role was not in the
# same play, and the per-user directories in /run were never removed.
echo
echo "enroot-paths.sh -- the paths enroot.conf sets, as the job user expands them"
ed="$work/enroot"; rm -rf "$ed"; mkdir -p "$ed/bin"
if render "shared/lib/enroot-paths.sh" "$ed/lib.sh"; then
    sed -i "s#^enroot_conf=.*#enroot_conf='$ed/enroot.conf'#" "$ed/lib.sh"
    # sudo -u USER sh -c CMD: run CMD here, with the job user's uid standing in.
    printf '#!/bin/sh\nshift 2\nexec "$@"\n' > "$ed/bin/sudo"
    printf '#!/bin/sh\necho 1001\n' > "$ed/bin/id"
    chmod +x "$ed/bin/sudo" "$ed/bin/id"
    cat > "$ed/enroot.conf" <<'CONF'
#ENROOT_RUNTIME_PATH        ${XDG_RUNTIME_DIR}/enroot
ENROOT_RUNTIME_PATH /run/enroot/old-$(id -u)
# BEGIN ANSIBLE MANAGED BLOCK (roles/enroot)
ENROOT_RUNTIME_PATH /run/enroot/user-$(id -u)
ENROOT_DATA_PATH /
CONF
    got=$(PATH="$ed/bin:$PATH" SLURM_JOB_USER=alice bash -c ". '$ed/lib.sh'
        enroot_user_path ENROOT_RUNTIME_PATH; echo rc=\$?
        enroot_user_path ENROOT_CACHE_PATH; echo rc=\$?
        enroot_user_path ENROOT_DATA_PATH; echo rc=\$?" 2>&1 | tr '\n' ' ')
    want="/run/enroot/user-1001 rc=0 rc=1 rc=1 "
    if [ "$got" = "$want" ]; then
        ok "the last setting wins, an unset key and a path at / are refused"
    else
        no "the last setting wins, an unset key and a path at / are refused" "got: $got"
    fi
    # Root clears only where no user can redirect the path; elsewhere the user does.
    got=$(bash -c ". '$ed/lib.sh'
        enroot_root_owned_above /usr/share/x; echo rc=\$?
        enroot_root_owned_above '$ed/x'; echo rc=\$?
        ln -s /usr '$ed/link'; enroot_root_owned_above '$ed/link/share/x'; echo rc=\$?" 2>&1 | tr '\n' ' ')
    want="rc=0 rc=1 rc=1 "
    if [ "$got" = "$want" ]; then
        ok "root clears only a path whose every parent is a real root-owned directory"
    else
        no "root clears only a path whose every parent is a real root-owned directory" "got: $got"
    fi
else
    no "enroot-paths.sh renders" "render failed"
fi
# The prolog never acts as root inside the world-writable parents: a planted file or
# symlink in the user's place is cleared without being followed, the directory is
# made by the user, and anything it cannot fix is logged, never a drained node.
ep="$ed/prolog"; rm -rf "$ep"; mkdir -p "$ep/bin" "$ep/run" "$ep/target"
printf '#!/bin/sh\nshift 2\nexec "$@"\n' > "$ep/bin/sudo"
printf '#!/bin/sh\nexit 0\n' > "$ep/bin/enroot"
printf '#!/bin/sh\necho "$*" >> "%s/log"\n' "$ep" > "$ep/bin/logger"
chmod +x "$ep/bin/"*
printf 'ENROOT_RUNTIME_PATH %s/run/user-x\n' "$ep" > "$ep/enroot.conf"
if render "shared/lib/enroot-paths.sh" "$ep/lib.sh" && render "prolog.d/50-all-enroot-dirs" "$ep/50" \
   && render "epilog.d/50-lastuserjob-all-enroot-dirs" "$ep/e50"; then
    sed -i "s#^enroot_conf=.*#enroot_conf='$ep/enroot.conf'#" "$ep/lib.sh"
    sed -i "s#/etc/slurm/shared/lib/enroot-paths.sh#$ep/lib.sh#" "$ep/50" "$ep/e50"
    me=$(id -u); run50() { PATH="$ep/bin:$PATH" SLURM_JOB_USER=u SLURM_JOB_UID="$1" bash "$ep/$2"; }
    echo secret > "$ep/target/f"
    ln -s "$ep/target" "$ep/run/user-x"
    run50 "$me" 50; rc1=$?
    t1=$([ -d "$ep/run/user-x" ] && [ ! -L "$ep/run/user-x" ] && [ -f "$ep/target/f" ] && echo ok)
    rm -rf "$ep/run/user-x"; echo planted > "$ep/run/user-x"
    run50 "$me" 50; rc2=$?
    t2=$([ -d "$ep/run/user-x" ] && echo ok)
    run50 99999 50; rc3=$?   # not the user's: cleared, and logged when it cannot be theirs
    t3=$(grep -q 'no private' "$ep/log" 2>/dev/null && echo ok)
    mkdir -p "$ep/run/user-x/sub"; run50 "$me" e50; rc4=$?
    t4=$([ ! -e "$ep/run/user-x" ] && echo ok)
    if [ "$rc1$rc2$rc3$rc4" = 0000 ] && [ "$t1$t2$t3$t4" = okokokok ]; then
        ok "a planted symlink or file is cleared unfollowed, a foreign one is logged, nothing fails"
    else
        no "a planted symlink or file is cleared unfollowed, a foreign one is logged, nothing fails" \
           "rc=$rc1$rc2$rc3$rc4 checks=$t1/$t2/$t3/$t4"
    fi
else
    no "enroot prolog renders" "render failed"
fi
# Under a per-job private directory the job never sees what the prolog would make on
# the host, so the prolog leaves the path to enroot inside the job.
if RENDER_PRIVATE_DIRS="$ep/run" render "prolog.d/50-all-enroot-dirs" "$ep/50p"; then
    sed -i "s#/etc/slurm/shared/lib/enroot-paths.sh#$ep/lib.sh#" "$ep/50p"
    rm -rf "$ep/run/user-x" "$ep/log"
    run50 "$(id -u)" 50p; rc=$?
    if [ "$rc" = 0 ] && [ ! -e "$ep/run/user-x" ] && [ ! -e "$ep/log" ]; then
        ok "a path under a per-job private directory is left to enroot in the job"
    else
        no "a path under a per-job private directory is left to enroot in the job" "rc=$rc"
    fi
else
    no "enroot prolog renders with private directories" "render failed"
fi
for t in prolog.d/50-all-enroot-dirs epilog.d/50-lastuserjob-all-enroot-dirs; do
    if render "$t" "$ed/script" && grep -q 'shared/lib/enroot-paths.sh' "$ed/script" \
       && ! grep -q '/run/enroot\|enroot-data\|enroot-cache' "$ed/script"; then
        ok "$t takes its paths from enroot.conf, none of its own"
    else
        no "$t takes its paths from enroot.conf, none of its own" "see $t"
    fi
done

# --- 26.09 snapshot -----------------------------------------------------------------
# scope=user must render byte-for-byte what 26.09 deployed. The snapshots are the
# 26.09 templates rendered with the same fixture values, then with the operator exemption
# read through shared/lib/localusers.sh (same decisions, both scopes) and logging through
# shared/lib/log.sh (it never fails a part, both scopes); script 41 is excluded
# because its match fix applies in both scopes and is covered above, and script 50
# because it now reads enroot.conf in both scopes (covered below), and run-parts.sh and
# epilog.sh because their exclusive detection, bounded squeue and never-failing logging
# changed in both scopes (covered by the dispatcher cases).
echo
echo "scope=user -- byte-identical to the 26.09 scripts"
snap_ok() {
    # $1 = template relative path, $2 = snapshot file name
    local out="$work/snap-$2"
    if RENDER_SCOPE=user render "$1" "$out" && sed -i "s#$work/shared-lib/log.sh#/etc/slurm/shared/lib/log.sh#" "$out" \
       && cmp -s "$out" "$here/snapshots/user-scope/$2"; then
        ok "scope=user renders $2 identically to 26.09"
    else
        no "scope=user renders $2 identically to 26.09" "$(diff "$here/snapshots/user-scope/$2" "$out" 2>&1 | head -5 | tr '\n' ' ')"
    fi
}
snap_ok "epilog.d/40-lastuserjob-processes"       40-lastuserjob-processes
snap_ok "epilog.d/42-lastuserjob-cleanup"         42-lastuserjob-cleanup

# Every template must render and parse in both scopes; StrictUndefined fails
# the render if a template references a variable the role does not define.
for scope in job user; do
    bad=""
    for t in epilog.sh epilog.d/40-lastuserjob-processes epilog.d/41-lastuserjob-ssh \
             epilog.d/42-lastuserjob-cleanup epilog.d/43-lastuserjob-ipc epilog.d/50-lastuserjob-all-enroot-dirs \
             shared/bin/run-parts.sh shared/lib/epilog-job-scope.sh shared/lib/enroot-paths.sh \
             shared/lib/localusers.sh; do
        RENDER_SCOPE=$scope render "$t" "$work/parse-$scope" 2>/dev/null || bad="$bad $t"
    done
    if [ -z "$bad" ]; then
        ok "all epilog templates render and parse with scope=$scope"
    else
        no "all epilog templates render and parse with scope=$scope" "failed:$bad"
    fi
done

# ---------------------------------------------------------------------------
# 41-lastuserjob-ssh: which SSH allow entries are removed
# ---------------------------------------------------------------------------
#
# The script edits two files, the live /etc/localusers and the localusers.backup
# exemption list. Both lookups used to match substrings and regular
# expressions: `grep -w` treated `je` as listed when `je.kim` was (the dot is
# not a word character), and `sed "/${user}/d"` deleted every entry that merely
# contained the name. The cases below pin whole-line, literal matching.
echo
echo "41-lastuserjob-ssh -- exact match"

setup_ssh() {
    local dir="$work/ssh"
    rm -rf "$dir"
    mkdir -p "$dir"
    write_logger_stub "$dir"
    render "epilog.d/41-lastuserjob-ssh" "$dir/41-ssh" || return 1
    render "shared/lib/localusers.sh" "$dir/localusers.sh" || return 1
    sed -i "s#^localusers_backup=.*#localusers_backup='$dir/localusers.backup'#" "$dir/localusers.sh"
    sed -i "s#/etc/slurm/shared/lib/localusers.sh#$dir/localusers.sh#" "$dir/41-ssh"
    sed -i "s#^localusers_live=.*#localusers_live='$dir/localusers'#" "$dir/41-ssh"
    if ! grep -qF "localusers_backup='$dir/localusers.backup'" "$dir/localusers.sh" \
       || ! grep -qF ". '$dir/localusers.sh'" "$dir/41-ssh" \
       || ! grep -qF "localusers_live='$dir/localusers'" "$dir/41-ssh"; then
        printf '  FAIL harness: the 41 paths were not redirected into the fixture\n' >&2
        return 1
    fi
    printf '%s' "$dir"
}

ssh_case() {
    # $1 = label, $2 = job user, $3 = backup list, $4 = live list,
    # $5 = expected live list afterwards
    local label="$1" user="$2" backup="$3" live="$4" want="$5" dir
    dir="$(setup_ssh)" || { no "$label" "harness setup failed"; return; }
    printf '%s\n' "$backup" > "$dir/localusers.backup"
    printf '%s\n' "$live" > "$dir/localusers"
    local out rc got
    out=$(PATH="$dir/bin:$PATH" SLURM_JOB_USER="$user" "$dir/41-ssh" 2>&1); rc=$?
    got="$(cat "$dir/localusers")"
    if [ "$rc" != 0 ]; then
        no "$label" "rc=$rc out=$(printf '%s' "$out" | tail -1)"
    elif [ "$got" != "$want" ]; then
        no "$label" "live list is '$(printf '%s' "$got" | tr '\n' ' ')', wanted '$(printf '%s' "$want" | tr '\n' ' ')'"
    else
        ok "$label"
    fi
}

ssh_case "the job user's own entry is removed" \
    alice "root" "root
alice
bob" "root
bob"
ssh_case "a name contained in other entries removes only its own line" \
    al "root" "root
al
alice
halley" "root
alice
halley"
ssh_case "a dot in the name is not a wildcard" \
    je.kim "root" "root
je.kim
jeXkim" "root
jeXkim"
ssh_case "a name with sed metacharacters is matched literally" \
    'a*b' "root" "root
a*b
aab" "root
aab"
ssh_case "a listed operator keeps the entry" \
    alice "alice" "root
alice" "root
alice"
ssh_case "a name that is only a prefix of a listed operator is not exempt" \
    je "je.kim" "root
je
je.kim" "root
je.kim"

# --- task prolog and the job runtime directory ----------------------------------------
# The task prolog's output becomes the task's environment, and a failing task prolog
# cancels the task: only export/unset lines may pass, and nothing may fail it.
echo
echo "task-prolog.sh and the runtime directory parts"
tp="$work/tp"; rm -rf "$tp"; mkdir -p "$tp/task-prolog.d" "$tp/runtime"
if render "task-prolog.sh" "$tp/task-prolog.sh" \
   && render "task-prolog.d/50-runtime-dir" "$tp/task-prolog.d/50-runtime-dir" \
   && render "epilog.d/50-lastuserjob-runtime-dir" "$tp/50-epilog"; then
    sed -i "s#'/etc/slurm'/task-prolog.d#'$tp'/task-prolog.d#" "$tp/task-prolog.sh"
    sed -i "s#'/run/slurm-users'#'$tp/runtime'#" "$tp/task-prolog.d/50-runtime-dir" "$tp/50-epilog"
    printf '#!/bin/sh\necho "export GOOD=1"\necho "print noise"\necho "export BAD-NAME=1"\necho "rm -rf /"\n' \
        > "$tp/task-prolog.d/10-mixed"
    printf '#!/bin/sh\necho "export ALSO=1"\nexit 3\n' > "$tp/task-prolog.d/20-fails"
    printf '#!/bin/sh\necho "export SKIPPED=1"\n' > "$tp/task-prolog.d/30-not-executable"
    chmod +x "$tp/task-prolog.d/10-mixed" "$tp/task-prolog.d/20-fails"
    me=$(id -u)
    out=$("$tp/task-prolog.sh"); rc=$?
    if [ "$rc" = 0 ] && [ "$out" = "export GOOD=1
export ALSO=1" ]; then
        ok "only export lines pass, a failing part is ignored, a non-executable part is skipped"
    else
        no "only export lines pass, a failing part is ignored, a non-executable part is skipped" "rc=$rc out=$out"
    fi
    rm -f "$tp/task-prolog.d/"[123]0-*
    out=$("$tp/task-prolog.sh")
    if [ -z "$out" ]; then
        ok "no runtime directory: XDG_RUNTIME_DIR is left alone"
    else
        no "no runtime directory: XDG_RUNTIME_DIR is left alone" "$out"
    fi
    mkdir -m 0700 "$tp/runtime/$me"
    out=$("$tp/task-prolog.sh")
    if [ "$out" = "export XDG_RUNTIME_DIR=$tp/runtime/$me" ]; then
        ok "the user's own runtime directory becomes XDG_RUNTIME_DIR"
    else
        no "the user's own runtime directory becomes XDG_RUNTIME_DIR" "$out"
    fi
    mkdir -p "$tp/elsewhere"; touch "$tp/elsewhere/keep"
    rmdir "$tp/runtime/$me"; ln -s "$tp/elsewhere" "$tp/runtime/$me"
    SLURM_JOB_UID=$me "$tp/50-epilog"
    if [ -e "$tp/elsewhere/keep" ]; then
        ok "the epilog never follows a symlink in place of the runtime directory"
    else
        no "the epilog never follows a symlink in place of the runtime directory" "target emptied"
    fi
    rm -f "$tp/runtime/$me"; mkdir -p "$tp/runtime/$me/containers"; touch "$tp/runtime/$me/containers/x"
    SLURM_JOB_UID=$me "$tp/50-epilog"
    if [ ! -e "$tp/runtime/$me" ]; then
        ok "the epilog of the user's last job removes the runtime directory"
    else
        no "the epilog of the user's last job removes the runtime directory" "still there"
    fi
else
    no "task prolog templates render" "render failed"
fi

echo
printf '%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
