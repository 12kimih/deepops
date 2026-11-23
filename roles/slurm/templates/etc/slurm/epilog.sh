#!/usr/bin/env bash
set -e
. '{{ slurm_config_dir }}/shared/lib/log.sh'
# slurmd runs it with almost no environment (deepops_system_path).
export PATH={{ deepops_system_path }}

part_log slurm-epilog "START user=$SLURM_JOB_USER job=$SLURM_JOB_ID"  # never fails the job
{{ slurm_config_dir }}/shared/bin/run-parts.sh {{ slurm_config_dir }}/epilog.d
part_log slurm-epilog "END user=$SLURM_JOB_USER job=$SLURM_JOB_ID"
