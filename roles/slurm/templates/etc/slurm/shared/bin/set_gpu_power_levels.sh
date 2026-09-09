#!/usr/bin/env bash
set -e

# A node capped by roles/nvidia_power_limit records its wattage here. Without it this
# script would hand an exclusive job the card maximum and then "restore" the card
# default on exit, leaving the node un-capped until the next reboot or Ansible run.
cap_file="{{ nvidia_power_limit_conf_file | default('/etc/nvidia-power-limit.conf') }}"
cap=""
if [ -r "$cap_file" ]; then
    cap="$(sed -n 's/^NVIDIA_POWER_LIMIT_WATTS=//p' "$cap_file" | tail -n 1)"
fi

gpu_count="$(nvidia-smi -L | wc -l)"

for i in $(seq 0 "$(( gpu_count - 1 ))" )
do
    case "$1" in
        max)
            next="$(nvidia-smi -i "$i" --query-gpu=power.max_limit --format=csv,noheader,nounits)"
            ;;
        default)
            next="$(nvidia-smi -i "$i" --query-gpu=power.default_limit --format=csv,noheader,nounits)"
            ;;
        min)
            next="$(nvidia-smi -i "$i" --query-gpu=power.min_limit --format=csv,noheader,nounits)"
            ;;
        *)
            echo "Usage: $0 [max,default,min]"
            exit 1
            ;;
    esac
    # On a capped node the cap is the ceiling for every level: "max" clamps down to it,
    # "default" restores it instead of the higher card default, and "min" is already
    # below it. Uncapped nodes keep the card's own values.
    if [ -n "$cap" ]; then
        next="$(awk -v want="$next" -v cap="$cap" 'BEGIN { print (want < cap) ? want : cap }')"
    fi
    nvidia-smi -i "$i" -pl "$next"
done
