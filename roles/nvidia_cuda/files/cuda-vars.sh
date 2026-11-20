#!/usr/bin/env bash
# roles/nvidia_cuda: the system-wide CUDA toolkit in every login shell

export PATH="/usr/local/cuda/bin${PATH:+:${PATH}}"
export LD_LIBRARY_PATH="/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
