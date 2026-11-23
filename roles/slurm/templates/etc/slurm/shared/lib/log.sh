# part_log TAG MESSAGE...: the message to syslog under TAG and to stderr (each part's
# output goes to the prolog/epilog log). Logging never fails a part: under "set -e" a
# logger that cannot reach /dev/log (journald restarting during an upgrade) exited 1,
# failed the part and drained the node (2026-11-23 code audit). Every part logs only
# through this.
part_log() {
    local tag=$1
    shift
    logger -s -t "$tag" -- "$*" || printf '%s: %s\n' "$tag" "$*" >&2 || :
}
