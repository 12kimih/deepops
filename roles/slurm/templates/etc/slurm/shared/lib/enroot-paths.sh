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

# enroot_owned_dir PATH: PATH is a real directory (not a symlink) owned by the job user.
enroot_owned_dir() {
    [ -d "$1" ] && [ ! -L "$1" ] && [ "$(stat -c %u -- "$1")" = "$SLURM_JOB_UID" ]
}

# enroot_clear PATH: remove what is at PATH as root without following a symlink or
# crossing a mount (a user's FUSE mount left inside). The parents are world-writable,
# so whatever is there may be another user's plant; nothing in it is trusted or kept.
enroot_clear() {
    rm -rf --one-file-system -- "$1"
}

enroot_log() {
    logger -t slurm-enroot "$*" 2>/dev/null || :
}
