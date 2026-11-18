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

# Configuration (Python package versions live in pyproject.toml / uv.lock)
CONFIG_DIR="${CONFIG_DIR:-${ROOT_DIR}/config}"            # Default configuration directory location
DEEPOPS_TAG="${1:-master}"                      # DeepOps branch to set up
VENV_DIR="${VENV_DIR:-/opt/deepops/env}"        # DeepOps environment (pyproject.toml / uv.lock)
KUBESPRAY_VENV_DIR="${KUBESPRAY_VENV_DIR:-/opt/deepops/kubespray-env}"  # Kubespray's own environment
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
# uv provides the Python interpreter and the locked environment; its version is the one
# pyproject.toml requires.
export PATH="${HOME}/.local/bin:${PATH}"
UV_VERSION="$(sed -n 's/^required-version = "==\(.*\)"/\1/p' "${REPO_DIR}/pyproject.toml" 2>/dev/null)"
if ! command -v uv >/dev/null 2>&1 || { [ -n "${UV_VERSION}" ] && [ "$(uv --version | awk '{print $2}')" != "${UV_VERSION}" ]; } ; then
    as_user "curl -LsSf https://astral.sh/uv/${UV_VERSION:+${UV_VERSION}/}install.sh | sh"
fi
command -v uv >/dev/null 2>&1 || { echo "ERROR: Unable to install uv" >&2; exit 1; }
sudo mkdir -p "${VENV_DIR}" "${KUBESPRAY_VENV_DIR}"
sudo chown -R $(id -u):$(id -g) "${VENV_DIR}" "${KUBESPRAY_VENV_DIR}"
deactivate nondestructive &> /dev/null

# Make it a uv venv in place first: uv sync replaces a directory that is not one by
# deleting it, which fails halfway when its parent is root-owned.
(cd "${REPO_DIR}" && uv venv -q --allow-existing "${VENV_DIR}" && UV_PROJECT_ENVIRONMENT="${VENV_DIR}" uv sync --locked) || { echo "ERROR: uv sync failed" >&2; exit 1; }
. "${VENV_DIR}/bin/activate"
# Run the repository checks on every commit (see .pre-commit-config.yaml).
(cd "${REPO_DIR}" && git rev-parse --git-dir >/dev/null 2>&1 && pre-commit install >/dev/null) || true

# Copy default configuration
if grep -i deepops README.md >/dev/null 2>&1 ; then
    if [ ! -d "${CONFIG_DIR}" ] ; then
        cp -rfp "${ROOT_DIR}/config.example" "${CONFIG_DIR}"
        echo "Copied default configuration to ${CONFIG_DIR}"
    else
        echo "Configuration directory '${CONFIG_DIR}' exists, not overwriting"
    fi
fi

# Install Ansible Galaxy roles
if command -v ansible-galaxy &> /dev/null ; then
    echo "Updating Ansible Galaxy roles..."
    initial_dir="$(pwd)"
    roles_path="${ROOT_DIR}/roles/galaxy"
    collections_path="${ROOT_DIR}/collections"

    cd "${ROOT_DIR}"
    as_user ansible-galaxy collection install -p "${collections_path}" --force -r "roles/requirements.yml" >/dev/null
    as_user ansible-galaxy role install -p "${roles_path}" --force -r "roles/requirements.yml" >/dev/null

    # Install any user-defined config requirements
    if [ -d "${CONFIG_DIR}" ] && [ -f "${CONFIG_DIR}/requirements.yml" ] ; then
        cd "${CONFIG_DIR}"
        as_user ansible-galaxy collection install -p "${collections_path}" --force -i -r "requirements.yml" >/dev/null
        as_user ansible-galaxy role install -p "${roles_path}" --force -i -r "requirements.yml" >/dev/null
    fi
    cd "${initial_dir}"
else
    echo "ERROR: Unable to install Ansible Galaxy roles, 'ansible-galaxy' command not found"
fi

# Update submodules
if command -v git &> /dev/null ; then
    as_user git submodule update --init
else
    echo "ERROR: Unable to update Git submodules, 'git' command not found"
fi

# Kubespray pins its own Ansible, so the Kubernetes playbooks run from a separate
# environment built from the submodule's requirements.txt.
if [ -f "${REPO_DIR}/submodules/kubespray/requirements.txt" ] ; then
    uv venv -q --allow-existing --python "${KUBESPRAY_PYTHON}" "${KUBESPRAY_VENV_DIR}"
    uv pip install -q --python "${KUBESPRAY_VENV_DIR}" -r "${REPO_DIR}/submodules/kubespray/requirements.txt"
fi

# Add Ansible virtual env to PATH when using Bash
if [ -f "${VENV_DIR}/bin/activate" ] ; then
    . "${VENV_DIR}/bin/activate"
    ansible localhost -m lineinfile -a "path=$HOME/.bashrc create=yes mode=0644 backup=yes line='source ${VENV_DIR}/bin/activate'"
fi

echo
echo "*** Setup complete ***"
echo "To use Ansible, run: source ${VENV_DIR}/bin/activate"
echo "For the Kubernetes playbooks, run: source ${KUBESPRAY_VENV_DIR}/bin/activate"
echo
