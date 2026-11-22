#!/usr/bin/env bash
# Slurm's TaskProlog: runs as the job user before every task of every step. Each part
# in task-prolog.d prints `export NAME=value` (or `unset NAME`) lines, which Slurm
# applies to the task's environment. Only those lines pass, and a failing part is
# skipped: a task prolog that fails cancels the task.
# https://slurm.schedmd.com/prolog_epilog.html
# The task's PATH is the user's; the parts get the system's.
export PATH={{ deepops_system_path }}
for part in '{{ slurm_config_dir }}'/task-prolog.d/*; do
    [ -x "$part" ] || continue
    timeout 10 "$part" 2>/dev/null | grep -E '^(export [A-Za-z_][A-Za-z0-9_]*=|unset [A-Za-z_][A-Za-z0-9_]*$)' || :
done
exit 0
