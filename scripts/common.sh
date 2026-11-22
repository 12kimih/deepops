#!/bin/bash
# Sourced at the top of the shell tools: the repository root, the site configuration
# and its whole-file defaults, the site's env.sh overrides, and a debug line.
# It sets no variable of its caller's own (SCRIPT_DIR stays the caller's).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
DEEPOPS_CONFIG_DIR="${DEEPOPS_CONFIG_DIR:-${ROOT_DIR}/config}"

# A whole file of the site configuration (helm values, manifests): the site's own when
# it has one, else config.example's, which is the default. config/ holds only what
# differs, so a site file equal to config.example's is a copy (config_lint.py refuses it).
deepops_config_file() {
  if [ -e "${DEEPOPS_CONFIG_DIR}/$1" ]; then
    printf '%s\n' "${DEEPOPS_CONFIG_DIR}/$1"
  else
    printf '%s\n' "${ROOT_DIR}/config.example/$1"
  fi
}

# The site's overrides of the tools' variables, version-controlled with its config.
if [ -f "${DEEPOPS_CONFIG_DIR}/env.sh" ]; then
  # shellcheck source=/dev/null
  source "${DEEPOPS_CONFIG_DIR}/env.sh"
fi

# The DeepOps version for debug output; config/env.sh may set it.
if [ -z "${DEEPOPS_VERSION}" ]; then
  DEEPOPS_VERSION="$(git -C "${ROOT_DIR}" describe --tags --always 2>/dev/null || echo unknown)"
fi

echo "Starting '${0}';  DeepOps version '${DEEPOPS_VERSION}'"
