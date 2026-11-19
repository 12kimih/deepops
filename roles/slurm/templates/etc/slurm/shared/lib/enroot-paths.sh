# shellcheck shell=bash
# Sourced by the enroot prolog and epilog. enroot.conf, which the enroot role writes, is
# the one place enroot's per-user paths are set; reading it here at job time keeps the
# prolog and epilog in step with it whichever play wrote it.

enroot_conf=/etc/enroot/enroot.conf

# enroot_user_path KEY: the job user's value of KEY (ENROOT_RUNTIME_PATH, ...), expanded
# by their shell as enroot expands it ("$(id -u)", "${HOME}"). Fails when enroot.conf
# does not set KEY: enroot's built-in default is then in use, which it manages itself.
enroot_user_path() {
    local value
    [ -r "$enroot_conf" ] || return 1
    value=$(awk -v key="$1" '$1 == key { sub(/^[^ \t]+[ \t]+/, ""); v = $0 } END { print v }' "$enroot_conf")
    [ -n "$value" ] || return 1
    value=$(sudo -u "$SLURM_JOB_USER" sh -c "echo \"$value\"") || return 1
    # Never a path that could reach beyond the user's own directory.
    case "$value" in
        /*/*) ;;
        *) return 1 ;;
    esac
    printf '%s\n' "$value"
}
