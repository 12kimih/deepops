#!/usr/bin/env bash
#
# config-diff.sh -- show how your private config/ overlay differs from the tracked
# config.example template, readably:
#   1) an overview of WHICH files differ
#   2) per file, a SIDE-BY-SIDE of only the differing lines  ( < config.example | > config )
#
# Usage:
#   scripts/deepops/config-diff.sh            # all differing files
#   scripts/deepops/config-diff.sh group_vars/slurm-cluster.yml   # just one file
#
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
A="${ROOT}/config.example"
B="${ROOT}/config"
WIDTH="${COLUMNS:-200}"
SUB="${1:-}"

[ -d "${B}" ] || { echo "No config/ yet -- run:  cp -rfp config.example config"; exit 1; }

echo "==================== files that differ (config/ vs config.example) ===================="
diff -rq "${A}/${SUB}" "${B}/${SUB}" 2>/dev/null | sed "s#${A}#config.example#g; s#${B}#config#g"

# per-file side-by-side of only the differing lines, for files present in BOTH
diff -rq "${A}/${SUB}" "${B}/${SUB}" 2>/dev/null \
  | sed -n 's/^Files .* and \(.*\) differ$/\1/p' \
  | while IFS= read -r cf; do
      rel="${cf#"${B}"/}"
      printf '\n==================== %s   ( < config.example  |  > config ) ====================\n' "${rel}"
      diff -y --suppress-common-lines -W "${WIDTH}" "${A}/${rel}" "${cf}"
    done
