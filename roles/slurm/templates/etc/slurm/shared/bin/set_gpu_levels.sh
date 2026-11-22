#!/usr/bin/env bash
# Every GPU's power limit and application clocks at one LEVEL: max for a job that has
# the node to itself (prolog), default after it (epilog).
#
#   set_gpu_levels.sh max|default
#
# One query reads every GPU, and nothing is written until each GPU nvidia-smi lists
# has its row: a failed or short query once configured some GPUs, or none, and still
# exited 0. Each GPU is then set on its own, all in parallel (nvidia-smi takes about a
# second per GPU and per setting). A setting a GPU does not support (N/A in the query,
# or nvidia-smi status 3, "not supported") is left alone, never a failure; so every
# GPU gets every setting it has, and the status says whether any of those failed.
set -u

level="${1:-}"
case "$level" in
    max) fields=index,power.max_limit,clocks.max.mem,clocks.max.sm ;;
    default) fields=index,power.default_limit ;;
    *)
        echo "Usage: $0 max|default" >&2
        exit 2
        ;;
esac

# A node capped by roles/nvidia_power_limit records its wattage here. The cap is the
# ceiling for every level: "max" clamps down to it and "default" restores it instead
# of the higher card default. Without it an exclusive job would get the card maximum
# and leave the node un-capped after it ends.
cap_file="{{ nvidia_power_limit_conf_file }}"
cap=""
if [ -r "$cap_file" ]; then
    cap="$(sed -n 's/^NVIDIA_POWER_LIMIT_WATTS=//p' "$cap_file" | tail -n 1)"
fi

# Both outputs are captured on their own: in a pipeline the status is the last
# command's, so a failed query read as an empty, successful one.
if ! rows_text="$(timeout 60 nvidia-smi --query-gpu="$fields" --format=csv,noheader,nounits)"; then
    echo "$0: querying $fields failed" >&2
    exit 1
fi
if ! gpu_list="$(timeout 60 nvidia-smi -L)"; then
    echo "$0: could not list the GPUs" >&2
    exit 1
fi
expected="$(grep -c '^GPU ' <<<"$gpu_list")" || expected=0
if [ "$expected" -eq 0 ]; then
    echo "$0: nvidia-smi -L reported no GPUs" >&2
    exit 1
fi

number='^[0-9]+(\.[0-9]+)?$'

# nvidia-smi ARGS... for one setting: status 3 is a setting this GPU does not have.
apply() {
    local rc=0
    timeout 60 nvidia-smi "$@" >/dev/null || rc=$?
    if [ "$rc" -ne 0 ] && [ "$rc" -ne 3 ]; then
        echo "$0: nvidia-smi $* failed ($rc)" >&2
        return 1
    fi
    return 0
}

# GPU INDEX POWER [MEM SM]: its settings in turn; a value that is not a number is N/A.
set_gpu() {
    local index="$1" power="$2" mem="${3:-}" sm="${4:-}" rc=0
    if [[ "$power" =~ $number ]]; then
        if [ -n "$cap" ]; then
            power="$(awk -v want="$power" -v cap="$cap" 'BEGIN { print (want < cap) ? want : cap }')"
        fi
        apply -i "$index" -pl "$power" || rc=1
    fi
    if [ "$level" = default ]; then
        apply -i "$index" -rac || rc=1
    elif [[ "$mem" =~ $number && "$sm" =~ $number ]]; then
        apply -i "$index" -ac "$mem,$sm" || rc=1
    fi
    return "$rc"
}

# Rows are counted as they are read rather than with bash's array-length expansion:
# this file is a template, and that expansion's brace-hash opens a Jinja comment.
rows=0
gpus=()
while IFS=', ' read -r index power mem sm _; do
    [ -z "$index" ] && continue
    if ! [[ "$index" =~ ^[0-9]+$ ]]; then
        echo "$0: unexpected row from nvidia-smi: $index, $power" >&2
        exit 1
    fi
    gpus+=("$index $power $mem $sm")
    rows=$((rows + 1))
done <<<"$rows_text"
if [ "$rows" -ne "$expected" ]; then
    echo "$0: got $rows rows for $fields, expected $expected" >&2
    exit 1
fi

pids=()
for gpu in "${gpus[@]}"; do
    # shellcheck disable=SC2086 # the row's fields, one word each
    set_gpu $gpu &
    pids+=("$!")
done
rc=0
for pid in "${pids[@]}"; do
    wait "$pid" || rc=1
done
exit "$rc"
