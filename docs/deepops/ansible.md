# [Ansible Guide](https://www.ansible.com/overview/how-ansible-works)

Ansible is a tool that automates the configuration of systems.

- [Ansible Guide](#ansible-guide)
  - [Ansible Setup](#ansible-setup)
    - [Requirements](#requirements)
    - [Passwordless configuration using SSH keys](#passwordless-configuration-using-ssh-keys)
    - [Password configuration](#password-configuration)
  - [Ansible Playbooks](#ansible-playbooks)
    - [Ansible playbook output](#ansible-playbook-output)
    - [Previewing a run (`--check --diff`)](#previewing-a-run---check---diff)
    - [Run history (ARA)](#run-history-ara)
  - [Ansible Usage](#ansible-usage)
    - [Useful commands](#useful-commands)
  - [Ansible Troubleshooting](#ansible-troubleshooting)
  - [Reference Documentation](#reference-documentation)

Install Ansible on any system which can access target nodes via SSH. This can be a laptop, small virtual machine, or cluster management server. This system is known in these docs as the provisioning node.

**Ansible is...**

- Agentless (there's nothing that needs to be installed on other nodes in the cluster)
- Idempotent (you can run the same playbook or task over and over again without repercussions - and tasks that do not require modification of the target nodes will result in Ansible skipping those tasks)
- Easy to maintain & scale (rather than custom scripts)
- Easy to read & use (via YAML playbooks, roles, and tasks)

## Ansible Setup

### Requirements

- Control machine with supported OS to run Ansible
- [Passwordless](#passwordless-configuration-using-ssh-keys) (SSH key) access from Ansible system to Universal GPU servers

`scripts/setup.sh` prepares the control machine on Ubuntu and RHEL-family systems. It installs [uv](https://docs.astral.sh/uv/), which provides the Python interpreter, then the locked environment in `.venv`, the Galaxy collections and roles, and Kubespray's environment in `.venv-kubespray`.

```bash
./scripts/setup.sh
```

The versions are set in one place each: Python tools in `pyproject.toml` and `uv.lock`, collections and Galaxy roles in `roles/requirements.yml` (installed by `scripts/deepops/galaxy.py`).

Commands in these docs are written as plain `ansible-playbook ...`. Run them through uv, `uv run ansible-playbook ...`, which first brings `.venv` in line with `uv.lock`; for an interactive shell, `source .venv/bin/activate` works too. The Kubernetes playbooks run from Kubespray's own environment instead: `source .venv-kubespray/bin/activate`.

### Passwordless configuration using SSH keys

Systems are easier to manage with Ansible if you don't have to type passwords. To configure SSH for passwordless
access using SSH keys. Run the following commands on the control machine where Ansible is installed:

```bash
# Generate an SSH keypair for the current user (hit enter to accept defaults)
ssh-keygen

# Copy the new SSH public key to each system that Ansible will configure
# where <username> is the remote username and <host> is the IP or hostname of the remote system
ssh-copy-id <username>@<host>
```

### Password configuration

To use Ansible without SSH keys, you can add flags to have ansible prompt for a password:

If SSH requires a password, add the `-k` flag

If sudo requires a password, add the `-K` flag

## Ansible Playbooks

[Ansible playbooks](https://docs.ansible.com/ansible/latest/user_guide/playbooks.html) are files which manage the configuration of remote machines.

### Ansible playbook output

- _Green_ indicates nothing changed as a result of the task
- _Yellow_ indicates something changed as a result of the task
- _Blue_ indicates the task was skipped
- _Red_ indicates the task failed

For more verbose output, add `-v`, `-vv`, `-vvv`, etc. flags.

A successful ansible-playbook run should provide a list of hosts changes and indicate no failures, for example:

```console
PLAY RECAP ************************************************************************************************************
localhost                  : ok=1    changed=0    unreachable=0    failed=0
node1                      : ok=401  changed=121  unreachable=0    failed=0
```

### Previewing a run (`--check --diff`)

`--check --diff` reports what a run would change without changing it. Ansible skips
`command` and `shell` tasks under `--check`, and this repository hides skipped tasks
(`display_skipped_hosts = False`), so a change made by a command would not show at
all. Tasks that change a host through a command therefore use `change_command` or
`change_shell` (`plugins/action`): they run exactly as `command` and `shell` do, and
under `--check` they report the command as a change, printed with `--diff`:

```console
TASK [bmc : Set the BMC LAN address] ****************************************
would run: ipmitool -I open lan set 1 defgw ipaddr 192.0.2.1
changed: [node1] => (item=defgw ipaddr)
```

A command whose outcome only its output tells (`kubectl apply`, `sacctmgr add`) is
reported as a change under `--check` too: nothing ran, so the preview cannot know.
`tests/task-rules` enforces the convention (rule 19).

A unit is started, stopped, enabled or reloaded through `systemd_unit`
(`plugins/action`, rule 22), which runs as `ansible.builtin.systemd_service`. Under
`--check` a unit this run installs (a package's, a template's) does not exist yet;
`systemd_unit` previews what it would do to it instead of failing:

```console
TASK [fail2ban : Run fail2ban] ************************************************
would enable fail2ban
would start fail2ban
changed: [node1]
```

Every in-tree module returns a diff when it changes something (rule 21), so `--diff`
shows what it would remove, write or install, never only that it would.

### Run history (ARA)

Every `ansible-playbook` and `ansible` run is recorded by
[ARA](https://ara.recordsansible.org/) (pinned in `pyproject.toml`): its command line
options (`--check`, `--diff`, `--limit`), each task's result per host, output, diffs
and the recap. Nobody has to copy terminal output to show what a run did, and the
history stays. `scripts/setup.sh` links `.callback_plugins` (`ansible.cfg`) to ARA's
callback; the records live in `~/.ara/server` (owner-only), one database per operator.

```bash
uv run ara playbook list                                  # runs, newest first
uv run ara result list --playbook <id> --status failed    # what failed
uv run ara result list --playbook <id> --changed          # what changed
uv run ara result show <id> --with-content                # one result: output, diff
uv run ara-manage runserver                               # the same in a browser (127.0.0.1:8000)
uv run ara playbook prune --days 365 --confirm            # drop old runs
```

Values a task marks `no_log` are recorded as censored and extra vars not at all; host
facts are kept with the run, as the fact cache keeps them. Test playbooks (`scripts/check.sh`,
`scripts/deepops/molecule.py`) leave no record: they point `ANSIBLE_CALLBACK_PLUGINS`
at an empty directory.

## Ansible Usage

_Create server inventory_

```bash
# Copy the default configuration
# This should be in the deepops directory and will be done automatically by setup.sh
cp -r config.example config

# Review and edit the inventory file to set IPs/hostnames for servers
cat config/inventory

# Review and edit configuration under config/group_vars/*.yml
cat config/group_vars/all.yml
cat config/group_vars/slurm-cluster.yml
```

After creating an inventory file all `ansible` and `ansible-playbook` commands must be run in the `./deepops` directory or the inventory file must be provided with the `-i` flag (`-i config/inventory`).

_Run Commands_

To run arbitrary commands in parallel across nodes in the cluster, you can use ansible and the groups or hosts defined in the inventory file, for example:

```bash
# ansible <host-group> -a hostname
ansible all -a hostname
```

_Run Playbooks_

To run playbooks, use the `ansible-playbook` command:

```bash
# If sudo requires a password, add the -K flag

# ansible-playbook <host-group> playbooks/<playbook>.yml
ansible-playbook -l k8s_cluster,localhost -b playbooks/k8s-cluster.yml
```

### Useful commands

_Debugging_

Show host vars: `ansible all -m debug -a 'var=hostvars'`

## Ansible Troubleshooting

_SSH Connection_

Ansible is configured by default in DeepOps to use SSH multiplexing to speed up connections. If a target system changes and you have a persistent connection (`ControlPersist` is set to 5m in `ansible.cfg`), you may need to clean up the control socket to prevent connection errors:

```bash
find ~/.ssh -type s -delete
```

_Fact cache inconsistencies_

By default Ansible caches facts about hosts for 24h to speed up provisioning. If a host's details change in that amount of time, you'll want to flush the fact cache so that Ansible will re-collect information about the host.

Add the `--flush-cache` flag to any `ansible-playbook` run to flush the fact cache and force Ansible to re-learn host information.

## Reference Documentation

Inventory reference: https://docs.ansible.com/ansible/latest/user_guide/intro_inventory.html

Variable configuration reference: https://docs.ansible.com/ansible/latest/user_guide/playbooks_variables.html
