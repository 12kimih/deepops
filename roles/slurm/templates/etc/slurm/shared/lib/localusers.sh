# shellcheck shell=bash
# Sourced by the epilog scripts that spare the accounts in localusers.backup (operators,
# whose login sessions on a node outlive their jobs).

localusers_backup='{{ slurm_config_dir }}/localusers.backup'

# localusers_listed USER: 0 when USER is listed, 1 when not, 2 when the list cannot be
# read, which is no evidence either way. The list is one account per line, matched as
# a whole line, literally: names contain dots (`je.kim`).
localusers_listed() {
    local rc=0
    grep -qxF -- "$1" "$localusers_backup" || rc=$?
    [ "$rc" -le 1 ] || rc=2
    return "$rc"
}
