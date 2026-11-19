# shellcheck shell=bash
# Sourced by the enroot prolog and epilog. enroot's configuration (enroot.conf, then
# enroot.conf.d/*.conf in name order, the last setting winning, as enroot reads them)
# is the one place its per-user paths are set; reading it here at job time keeps the
# prolog and epilog in step with it whichever play wrote it.

enroot_conf=/etc/enroot/enroot.conf

# enroot_user_path KEY: the job user's value of KEY (ENROOT_RUNTIME_PATH, ...), expanded
# by their shell as enroot expands it ("$(id -u)", "${HOME}"). Fails when no file sets
# KEY: enroot's built-in default is then in use, which it manages itself.
enroot_user_path() {
    local value files
    files=$(ls -1 "$enroot_conf" "$enroot_conf".d/*.conf 2>/dev/null) || :
    [ -n "$files" ] || return 1
    # shellcheck disable=SC2086 # the paths have no spaces (enroot's own layout)
    value=$(awk -v key="$1" '$1 == key { sub(/^[^ \t]+[ \t]+/, ""); v = $0 } END { print v }' $files)
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

# enroot_root_owned_above PATH: every directory above PATH is a real directory root owns,
# so no user can redirect PATH (a symlinked parent would turn `rm` into removing
# whatever root-owned directory it points into).
enroot_root_owned_above() {
    local dir
    dir=$(dirname -- "$1")
    while :; do
        [ -d "$dir" ] && [ ! -L "$dir" ] && [ "$(stat -c %u -- "$dir")" = 0 ] || return 1
        [ "$dir" = / ] && return 0
        dir=$(dirname -- "$dir")
    done
}

# enroot_clear PATH: remove what is at PATH without following a symlink or crossing a
# mount (a user's FUSE mount left inside). Under a world-writable root-owned parent
# (the defaults) whatever is there may be another user's plant, so root removes it,
# and only when every directory above is root's. Anywhere else (a ${HOME} path) the
# job user removes it, which can reach nothing they could not remove themselves.
enroot_clear() {
    if enroot_root_owned_above "$1"; then
        rm -rf --one-file-system -- "$1"
    else
        sudo -u "$SLURM_JOB_USER" rm -rf --one-file-system -- "$1"
    fi
}

enroot_log() {
    logger -t slurm-enroot "$*" 2>/dev/null || :
}
