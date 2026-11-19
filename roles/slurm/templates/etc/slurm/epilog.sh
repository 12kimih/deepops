#!/usr/bin/env bash
set -e

logger -s -t slurm-epilog "START user=$SLURM_JOB_USER job=$SLURM_JOB_ID" || :  # never fails the job
{{ slurm_config_dir }}/shared/bin/run-parts.sh {{ slurm_config_dir }}/epilog.d
logger -s -t slurm-epilog "END user=$SLURM_JOB_USER job=$SLURM_JOB_ID" || :
