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
# shellcheck source=/dev/null
. /etc/os-release
DEPS_DEB=(git curl sshpass wget)
DEPS_EL=(git curl python3-libselinux sshpass wget)
EPEL_VERSION="${VERSION_ID%%.*}"
EPEL_URL="https://dl.fedoraproject.org/pub/epel/epel-release-latest-${EPEL_VERSION}.noarch.rpm"
PROXY_FILE="${SCRIPT_DIR}/deepops/proxy.sh"

# Disable interactive prompts from Apt
export DEBIAN_FRONTEND=noninteractive

# Exit if run as root
if [ "$(id -u)" -eq 0 ] ; then
    echo "Please run as a regular user" >&2
    exit 1
fi

# Run a command (its words as given, never re-parsed) as root or as this user, with the
# proxy settings of deepops/proxy.sh when it sets any.
proxy_in_use() { grep -qvE '^[[:space:]]*(#|$)' "${PROXY_FILE}" 2>/dev/null; }
as_sudo() {
    if proxy_in_use ; then
        sudo -H bash -c '. "$0" && exec "$@"' "${PROXY_FILE}" "$@"
    else
        sudo "$@"
    fi
}
as_user() {
    if proxy_in_use ; then
        bash -c '. "$0" && exec "$@"' "${PROXY_FILE}" "$@"
    else
        "$@"
    fi
}

# Install software dependencies
case "$ID" in
    rhel*|centos*)
        as_sudo yum -y -q install "${EPEL_URL}"     # EPEL, for sshpass
        as_sudo yum -y -q install "${DEPS_EL[@]}"
        ;;
    ubuntu*)
        as_sudo apt-get -q update
        as_sudo apt-get -yq install "${DEPS_DEB[@]}"
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
            as_user git clone --branch "${DEEPOPS_TAG}" https://github.com/NVIDIA/deepops.git
        fi
        cd deepops || exit 1
    else
        echo "ERROR: Unable to check out DeepOps git repo, 'git' command not found"
        exit
    fi
fi

# Install the locked Python environment from the checkout (standalone runs have just cloned it)
if (cd "${SCRIPT_DIR}/.." && grep -qi deepops README.md 2>/dev/null) ; then
    REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
else
    REPO_DIR="$(pwd)"
fi
# uv provides the Python interpreter and the locked environment (.venv); its own
# version is the one pyproject.toml requires.
export PATH="${HOME}/.local/bin:${PATH}"
UV_VERSION="$(sed -n 's/^required-version = "==\(.*\)"/\1/p' "${REPO_DIR}/pyproject.toml" 2>/dev/null)"
if ! command -v uv >/dev/null 2>&1 || { [ -n "${UV_VERSION}" ] && [ "$(uv --version | awk '{print $2}')" != "${UV_VERSION}" ]; } ; then
    # shellcheck disable=SC2016  # $0 is the URL, expanded by the inner shell
    as_user bash -c 'set -o pipefail; curl -LsSf "$0" | sh' "https://astral.sh/uv/${UV_VERSION:+${UV_VERSION}/}install.sh"
fi
command -v uv >/dev/null 2>&1 || { echo "ERROR: Unable to install uv" >&2; exit 1; }
cd "${REPO_DIR}" || exit 1
uv sync --locked || { echo "ERROR: uv sync failed" >&2; exit 1; }
# Every Ansible run is recorded with ARA through plugins/callback (ansible.cfg). Earlier
# versions linked .callback_plugins to the ARA package's callback directory: that link
# goes, and nothing else by that name.
case "$(readlink .callback_plugins 2>/dev/null)" in
    */ara/plugins/callback) rm -f .callback_plugins ;;
esac
# The tools scripts/check.sh runs from outside .venv, the missing ones (CI does the same).
mapfile -t CHECK_DEB < <(scripts/check.sh --missing-tools)
if [ "${#CHECK_DEB[@]}" -gt 0 ] ; then
    case "$ID" in
        ubuntu*|debian*) as_sudo apt-get -yq install --no-install-recommends "${CHECK_DEB[@]}" ;;
        *) echo "NOTE: scripts/check.sh also needs (Debian names): ${CHECK_DEB[*]}" >&2 ;;
    esac
fi
# git hooks live in the clone's common directory, shared by all its worktrees, and run
# scripts from the tree that installed them; so only the main worktree installs them
# (one run from a linked worktree pointed every worktree's hooks into a tree that could
# be removed). install_hook links one; a hook of someone else's is left alone and said.
MAIN_WORKTREE="$(git -C "${REPO_DIR}" worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')"
install_hook() {  # <repository> <hook name> <script under scripts/deepops>
    hooks="$(git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)/hooks" || return 0
    if [ -n "$(git -C "$1" config --get core.hooksPath)" ] ; then
        echo "WARNING: core.hooksPath is set in $1; its $2 hook is not installed" >&2
        return 0
    fi
    hook="${hooks}/$2"
    want="${REPO_DIR}/scripts/deepops/$3"
    if [ ! -e "${hook}" ] || [ "$(readlink -f "${hook}")" = "${want}" ] || grep -qs 'File generated by pre-commit' "${hook}" ; then
        mkdir -p "${hooks}" && ln -sfn "${want}" "${hook}"
    else
        echo "WARNING: ${hook} is another hook; $3 is not installed there" >&2
    fi
}
if [ -z "${MAIN_WORKTREE}" ] ; then
    :  # not a git checkout
elif [ "$(cd "${MAIN_WORKTREE}" && pwd -P)" != "$(cd "${REPO_DIR}" && pwd -P)" ] ; then
    echo "NOTE: git hooks are installed from the main worktree (${MAIN_WORKTREE}), not from here" >&2
else
    # The repository checks on every commit (see .pre-commit-config.yaml), through git's
    # own hooks: pre-commit.sh on a copy of what is staged, commit-msg.sh on the message.
    # They include the guard that keeps site data out of this public repository;
    # install_hook says when it cannot install one, and replaces pre-commit's own hooks.
    install_hook "${REPO_DIR}" pre-commit pre-commit.sh
    install_hook "${REPO_DIR}" commit-msg commit-msg.sh
    # The push guards see every ref a push sends (pre-push.sh), and a branch checked
    # out in one worktree moves only from there (branch-guard.sh).
    install_hook "${REPO_DIR}" pre-push pre-push.sh
    install_hook "${REPO_DIR}" reference-transaction branch-guard.sh
fi

# Start the site configuration with what Ansible reads from the inventory directory
# alone: the inventory and its variables (config.example's settings, to edit). Every
# other file of config.example is the default the tools read while the site has none
# (scripts/common.sh deepops_config_file), so a copy would only hide its updates; a
# site adds a file there to change it. config_lint.py's INVENTORY_DIR names the same.
if grep -i deepops README.md >/dev/null 2>&1 ; then
    if [ ! -d "${CONFIG_DIR}" ] ; then
        mkdir -p "${CONFIG_DIR}"
        for part in inventory group_vars host_vars ; do
            cp -rp "${ROOT_DIR}/config.example/${part}" "${CONFIG_DIR}/"
        done
        echo "Created ${CONFIG_DIR} from config.example (inventory, group_vars, host_vars)"
    else
        echo "Configuration directory '${CONFIG_DIR}' exists, not overwriting"
    fi
fi

# The site config, when it is a git repository, checks its own commits with the same
# settings checks (scripts/deepops/config-pre-commit.sh).
if [ -d "${CONFIG_DIR}/.git" ] && [ -n "${MAIN_WORKTREE}" ] && [ "$(cd "${MAIN_WORKTREE}" && pwd -P)" = "$(cd "${REPO_DIR}" && pwd -P)" ] ; then
    install_hook "${CONFIG_DIR}" pre-commit config-pre-commit.sh
    install_hook "${CONFIG_DIR}" reference-transaction branch-guard.sh
    # Its commit messages follow deepops's rule, checked as made and as pushed.
    install_hook "${CONFIG_DIR}" commit-msg commit_message.py
    install_hook "${CONFIG_DIR}" pre-push commit_message.py
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
    # The same ARA as .venv (uv.lock), so the shared callback loads there too.
    uv pip install -q --python .venv-kubespray "ara[server]==$(uv run --locked python -c 'import importlib.metadata as m; print(m.version("ara"))')"
fi

# Earlier versions activated /opt/deepops/env from ~/.bashrc; uv run replaces that. Only
# a file that has the line is edited, and no backup copy is left beside it.
if grep -qx 'source /opt/deepops/env/bin/activate' "${HOME}/.bashrc" 2>/dev/null ; then
    sed -i '\|^source /opt/deepops/env/bin/activate$|d' "${HOME}/.bashrc"
fi

echo
echo "*** Setup complete ***"
echo "Run Ansible through uv, which keeps .venv equal to uv.lock:  uv run ansible-playbook ..."
echo "For the Kubernetes playbooks:  source .venv-kubespray/bin/activate"
echo
