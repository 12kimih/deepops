#!/usr/bin/env bash

# DeepOps setup/bootstrap script
#   This script installs required dependencies on a system so it can run Ansible
#   and initializes the DeepOps directory
#
# Can be run standalone with: curl -sL bit.ly/nvdeepops | bash
#                         or: curl -sL bit.ly/nvdeepops | bash -s -- 19.07

# Determine current directory and root directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
ROOT_DIR="${SCRIPT_DIR}/.."

# Configuration (Python and tool versions live in pyproject.toml / uv.lock, collections
# and Galaxy roles in roles/requirements.yml)
# The site config directory; every tool reads DEEPOPS_CONFIG_DIR (scripts/common.sh too).
CONFIG_DIR="${DEEPOPS_CONFIG_DIR:-${ROOT_DIR}/config}"
DEEPOPS_TAG="${1:-master}"                      # DeepOps branch to set up
KUBESPRAY_PYTHON="${KUBESPRAY_PYTHON:-3.13}"    # Newest Python the pinned Kubespray supports

###

# Set distro-specific variables
. /etc/os-release
DEPS_DEB=(git curl sshpass wget)
DEPS_EL=(git curl python3-libselinux sshpass wget)
EPEL_VERSION="$(echo ${VERSION_ID} | sed  's/^[^0-9]*//;s/[^0-9].*$//')"
EPEL_URL="https://dl.fedoraproject.org/pub/epel/epel-release-latest-${EPEL_VERSION}.noarch.rpm"
PROXY_USE=`grep -v ^# ${SCRIPT_DIR}/deepops/proxy.sh 2>/dev/null | grep -v ^$ | wc -l`

# Disable interactive prompts from Apt
export DEBIAN_FRONTEND=noninteractive

# Exit if run as root
if [ $(id -u) -eq 0 ] ; then
    echo "Please run as a regular user" >&2
    exit 1
fi

# Proxy wrapper
as_sudo(){
    if [ $PROXY_USE -gt 0 ] ; then
        cmd="sudo -H bash -c '. ${SCRIPT_DIR}/deepops/proxy.sh && $@'"
    else
        cmd="sudo bash -c '$@'"
    fi
    eval $cmd
}

# Proxy wrapper
as_user(){
    if [ $PROXY_USE -gt 0 ] ; then
        cmd="bash -c '. ${SCRIPT_DIR}/deepops/proxy.sh && $@'"
    else
        cmd="bash -c '$@'"
    fi
    eval $cmd
}

# Install software dependencies
case "$ID" in
    rhel*|centos*)
        as_sudo "yum -y -q install ${EPEL_URL} |& grep -v 'Nothing to do'"       # Enable EPEL (required for sshpass package)
        as_sudo "yum -y -q install ${DEPS_EL[@]}"
        ;;
    ubuntu*)
        as_sudo "apt-get -q update"
        as_sudo "apt-get -yq install ${DEPS_DEB[@]}"
        ;;
    *)
        # Not an error: this branch is an escape hatch. Everything after this
        # case is distro-agnostic, so the run continues and the hard gate on
        # uv bootstrap below reports a genuine missing dependency. Keeping it
        # non-fatal is what lets Rocky, AlmaLinux, Debian and friends work.
        echo "WARNING: no automatic dependency install for OS '${ID}' (ID_LIKE='${ID_LIKE:-none}')" >&2
        echo "If setup fails below, install these manually and re-run:" >&2
        echo "  RPM-based: ${DEPS_EL[*]}" >&2
        echo "  DEB-based: ${DEPS_DEB[*]}" >&2
        ;;
esac

# Clone DeepOps git repo if running standalone
if ! (cd "${SCRIPT_DIR}/.." && grep -i deepops README.md >/dev/null 2>&1 ) ; then
    if command -v git &> /dev/null ; then
        if ! test -d deepops ; then
            as_user git clone --branch ${DEEPOPS_TAG} https://github.com/NVIDIA/deepops.git
        fi
        cd deepops
    else
        echo "ERROR: Unable to check out DeepOps git repo, 'git' command not found"
        exit
    fi
fi

# Install the locked Python environment from the checkout (standalone runs have just cloned it)
REPO_DIR="$( (cd "${SCRIPT_DIR}/.." && grep -qi deepops README.md 2>/dev/null) && cd "${SCRIPT_DIR}/.." && pwd || pwd)"
# uv provides the Python interpreter and the locked environment (.venv); its own
# version is the one pyproject.toml requires.
export PATH="${HOME}/.local/bin:${PATH}"
UV_VERSION="$(sed -n 's/^required-version = "==\(.*\)"/\1/p' "${REPO_DIR}/pyproject.toml" 2>/dev/null)"
if ! command -v uv >/dev/null 2>&1 || { [ -n "${UV_VERSION}" ] && [ "$(uv --version | awk '{print $2}')" != "${UV_VERSION}" ]; } ; then
    as_user "curl -LsSf https://astral.sh/uv/${UV_VERSION:+${UV_VERSION}/}install.sh | sh"
fi
command -v uv >/dev/null 2>&1 || { echo "ERROR: Unable to install uv" >&2; exit 1; }
cd "${REPO_DIR}"
uv sync --locked || { echo "ERROR: uv sync failed" >&2; exit 1; }
# Run the repository checks on every commit (see .pre-commit-config.yaml). They include
# the guard that keeps site data out of this public repository, so a failed install is
# said, never swallowed: pre-commit refuses, for one, when core.hooksPath is set.
if git rev-parse --git-dir >/dev/null 2>&1 ; then
    if ! hook_out="$(uv run pre-commit install 2>&1)" ; then
        echo "WARNING: the commit hooks were not installed, so nothing checks commits:" >&2
        echo "${hook_out}" >&2
    fi
fi

# Copy default configuration
if grep -i deepops README.md >/dev/null 2>&1 ; then
    if [ ! -d "${CONFIG_DIR}" ] ; then
        cp -rfp "${ROOT_DIR}/config.example" "${CONFIG_DIR}"
        echo "Copied default configuration to ${CONFIG_DIR}"
    else
        echo "Configuration directory '${CONFIG_DIR}' exists, not overwriting"
    fi
fi

# Install the Galaxy collections and roles (roles/requirements.yml, then config/requirements.yml)
echo "Installing Ansible Galaxy collections and roles..."
DEEPOPS_CONFIG_DIR="${CONFIG_DIR}" uv run scripts/deepops/galaxy.py >/dev/null || { echo "ERROR: Galaxy install failed" >&2; exit 1; }

# Update submodules
if command -v git &> /dev/null ; then
    as_user git submodule update --init
else
    echo "ERROR: Unable to update Git submodules, 'git' command not found"
fi

# Kubespray pins its own Ansible, so the Kubernetes playbooks run from a separate
# environment, rebuilt from the submodule's requirements.txt on every run.
if [ -f submodules/kubespray/requirements.txt ] ; then
    uv venv -q --clear --python "${KUBESPRAY_PYTHON}" .venv-kubespray
    uv pip install -q --python .venv-kubespray -r submodules/kubespray/requirements.txt
fi

# Earlier versions activated /opt/deepops/env from ~/.bashrc; uv run replaces that.
sed -i.bak '\|^source /opt/deepops/env/bin/activate$|d' "${HOME}/.bashrc" 2>/dev/null || true

echo
echo "*** Setup complete ***"
echo "Run Ansible through uv, which keeps .venv equal to uv.lock:  uv run ansible-playbook ..."
echo "For the Kubernetes playbooks:  source .venv-kubespray/bin/activate"
echo
