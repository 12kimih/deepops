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

# Configuration (Python package versions live in requirements.txt / requirements-dev.txt)
CONFIG_DIR="${CONFIG_DIR:-${ROOT_DIR}/config}"            # Default configuration directory location
DEEPOPS_TAG="${1:-master}"                      # DeepOps branch to set up
PIP="${PIP:-pip3}"                              # Pip binary to use
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"    # Python3 path
VENV_DIR="${VENV_DIR:-/opt/deepops/env}"        # Path to python virtual environment to create

###

# Set distro-specific variables
. /etc/os-release
DEPS_DEB=(git virtualenv python3-virtualenv sshpass wget)
DEPS_EL=(git python3-libselinux python3-virtualenv sshpass wget)
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
        # `virtualenv` below reports a genuine missing dependency. Keeping it
        # non-fatal is what lets Rocky, AlmaLinux, Debian and friends work.
        echo "WARNING: no automatic dependency install for OS '${ID}' (ID_LIKE='${ID_LIKE:-none}')" >&2
        echo "If setup fails below, install these manually and re-run:" >&2
        echo "  RPM-based: ${DEPS_EL[*]}" >&2
        echo "  DEB-based: ${DEPS_DEB[*]}" >&2
        ;;
esac

# Create virtual environment and install python dependencies
if command -v virtualenv &> /dev/null ; then
    sudo mkdir -p "${VENV_DIR}"
    sudo chown -R $(id -u):$(id -g) "${VENV_DIR}"
    deactivate nondestructive &> /dev/null
    virtualenv -q --python="${PYTHON_BIN}" "${VENV_DIR}"
    . "${VENV_DIR}/bin/activate"
    as_user "${PIP} install -q --upgrade pip"
else
    echo "ERROR: Unable to create Python virtual environment, 'virtualenv' command not found"
    exit 1
fi

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

# Install the pinned Python packages from the checkout (standalone runs have just cloned it)
REPO_DIR="$( (cd "${SCRIPT_DIR}/.." && grep -qi deepops README.md 2>/dev/null) && cd "${SCRIPT_DIR}/.." && pwd || pwd)"
if [ -f "${VENV_DIR}/bin/activate" ] ; then
    . "${VENV_DIR}/bin/activate"
    as_user "${PIP} install -q -r ${REPO_DIR}/requirements-dev.txt"
    # Run the repository checks on every commit (see .pre-commit-config.yaml).
    (cd "${REPO_DIR}" && git rev-parse --git-dir >/dev/null 2>&1 && pre-commit install >/dev/null) || true
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

# Add Ansible virtual env to PATH when using Bash
if [ -f "${VENV_DIR}/bin/activate" ] ; then
    . "${VENV_DIR}/bin/activate"
    ansible localhost -m lineinfile -a "path=$HOME/.bashrc create=yes mode=0644 backup=yes line='source ${VENV_DIR}/bin/activate'"
fi

echo
echo "*** Setup complete ***"
echo "To use Ansible, run: source ${VENV_DIR}/bin/activate"
echo
