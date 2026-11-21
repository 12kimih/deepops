# shellcheck shell=bash
# Sourced by the epilog parts that spare the operators listed in localusers.backup: their
# login sessions on a node outlive their jobs, so a user's last job must not kill their
# processes (40), revoke their SSH access (41), delete their files (42) or their IPC
# objects (43). The one decision, so no part reads the list its own way.

localusers_backup='{{ slurm_config_dir }}/localusers.backup'

# localusers_spared USER: 0 when the epilog leaves USER alone, 1 when it may clean up.
# A list that cannot be read is no evidence of absence: USER is spared, and it is
# logged. The list is one account per line, matched as a whole line, literally (names
# contain dots: `je.kim`; `grep -w` or an unanchored sed match substrings and regexes).
localusers_spared() {
    local rc=0
    grep -qxF -- "$1" "$localusers_backup" || rc=$?
    case "$rc" in
        0) return 0 ;;
        1) return 1 ;;
    esac
    logger -s -t slurm-epilog "localusers lookup failed (grep rc=${rc}, ${localusers_backup}); leaving $1 alone" || :
    return 0
}
