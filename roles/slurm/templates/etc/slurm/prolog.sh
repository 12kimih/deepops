#!/usr/bin/env bash
set -e
# slurmd runs it with almost no environment (deepops_system_path).
export PATH={{ deepops_system_path }}

logger -s -t slurm-prolog "START user=$SLURM_JOB_USER job=$SLURM_JOB_ID" || :  # never fails the job
{{ slurm_config_dir }}/shared/bin/run-parts.sh {{ slurm_config_dir }}/prolog.d
logger -s -t slurm-prolog "END user=$SLURM_JOB_USER job=$SLURM_JOB_ID" || :
