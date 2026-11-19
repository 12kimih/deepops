#!/usr/bin/env bash
# Install the Helm client on this (admin) machine at the version the bundled Kubespray
# installs on the cluster, from the official release tarball, verified against the
# checksum Kubespray pins for it: one pin, nothing run from the network.
#   HELM_DOWNLOAD_BASE  where the tarballs are (default https://get.helm.sh; a mirror in
#                       an airgap)
#   HELM_INSTALL_DIR    where helm goes (default /usr/local/bin)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
ROOT_DIR="${SCRIPT_DIR}/../.."
CHECKSUMS="${ROOT_DIR}/submodules/kubespray/roles/kubespray_defaults/vars/main/checksums.yml"
HELM_DOWNLOAD_BASE="${HELM_DOWNLOAD_BASE:-https://get.helm.sh}"
HELM_INSTALL_DIR="${HELM_INSTALL_DIR:-/usr/local/bin}"

case "$(uname -m)" in
    x86_64) arch=amd64 ;;
    aarch64 | arm64) arch=arm64 ;;
    *) echo "Unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac

if [ ! -r "${CHECKSUMS}" ]; then
    echo "${CHECKSUMS} is missing: run git submodule update --init --recursive" >&2
    exit 1
fi

# Kubespray's helm_version is the first entry under helm_archive_checksums.<arch>.
read -r version checksum < <(awk -v arch="${arch}" '
    /^helm_archive_checksums:/ { block = 1; next }
    block && /^[^ ]/ { exit }
    block && $0 ~ "^  " arch ":$" { in_arch = 1; next }
    in_arch && /^    [0-9]/ { sub(":", "", $1); sub("^sha256:", "", $2); print $1, $2; exit }
' "${CHECKSUMS}")
if [ -z "${version:-}" ] || [ -z "${checksum:-}" ]; then
    echo "No Helm version for ${arch} in ${CHECKSUMS}" >&2
    exit 1
fi

if command -v helm >/dev/null 2>&1 && [ "$(helm version --template '{{.Version}}' 2>/dev/null)" = "v${version}" ]; then
    echo "Helm v${version} is installed."
    exit 0
fi

for tool in curl sha256sum tar; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        echo "${tool} is required" >&2
        exit 1
    fi
done

# A private directory: what is unpacked here is installed with sudo.
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
tarball="helm-v${version}-linux-${arch}.tar.gz"
curl -fsSL --retry 3 -o "${tmp}/${tarball}" "${HELM_DOWNLOAD_BASE}/${tarball}"
echo "${checksum}  ${tmp}/${tarball}" | sha256sum -c --quiet -
tar -xzf "${tmp}/${tarball}" -C "${tmp}"
sudo install -D -m 0755 "${tmp}/linux-${arch}/helm" "${HELM_INSTALL_DIR}/helm"
"${HELM_INSTALL_DIR}/helm" version --short
