#!/usr/bin/env bash
# Generate a CA certificate and key for the docker registry cache
# (nginx_docker_cache_ca, docs/container/nginx-docker-cache.md).
#
#   gen-ca.sh [OUTPUT_DIR]   default: a new private directory from mktemp -d
#
# The key is written mode 0600 (a new directory is mode 0700), and an existing
# ca.key or ca.crt there is never overwritten.

# Source common libraries and env variables
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
ROOT_DIR="${SCRIPT_DIR}/../.."
source "${ROOT_DIR}/scripts/common.sh"
set -euo pipefail

umask 077
OUT_DIR="${1:-$(mktemp -d)}"
mkdir -p "${OUT_DIR}"
CA_CRT_OUTFILE="${OUT_DIR}/ca.crt"
CA_KEY_OUTFILE="${OUT_DIR}/ca.key"
for f in "${CA_KEY_OUTFILE}" "${CA_CRT_OUTFILE}"; do
  if [ -e "${f}" ]; then
    echo "ERROR: ${f} already exists; remove it or pass another directory" >&2
    exit 1
  fi
done

# Not a secret: the proxy image (rpardini/docker-registry-proxy, create_ca_cert.sh)
# opens the CA key with this fixed passphrase, so a key under any other one breaks it.
# The key is protected by its file mode, here and on the cache server (0400).
PROXY_KEY_PASSPHRASE="foobar"

echo "Generating CA key"
openssl genrsa -des3 -passout "pass:${PROXY_KEY_PASSPHRASE}" -out "${CA_KEY_OUTFILE}" 4096

echo "Generating CA certificate"
openssl req -new -x509 -days 1300 -sha256 -key "${CA_KEY_OUTFILE}" -out "${CA_CRT_OUTFILE}" -passin "pass:${PROXY_KEY_PASSPHRASE}" -subj "/C=US/ST=California/L=Santa Clara/O=DeepOps/OU=HPC/CN=DeepOps" -extensions IA -config <(
cat <<-EOF
[req]
distinguished_name = dn
[dn]
[IA]
basicConstraints = critical,CA:TRUE
keyUsage = critical, digitalSignature, cRLSign, keyCertSign
subjectKeyIdentifier = hash
EOF
)
chmod 0644 "${CA_CRT_OUTFILE}"

echo "CA key written out to: ${CA_KEY_OUTFILE}"
echo "CA crt written out to: ${CA_CRT_OUTFILE}"
