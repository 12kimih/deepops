# Testing

DeepOps Testing, CI/CD, and Validation

- [Testing](#testing)
  - [Linting](#linting)
  - [DeepOps end-to-end testing](#deepops-end-to-end-testing)
    - [Testing Method](#testing-method)
    - [Tests](#tests)
  - [DeepOps Ansible role testing](#deepops-ansible-role-testing)
    - [Running the scenarios](#running-the-scenarios)
    - [Defining Molecule tests for a role](#defining-molecule-tests-for-a-role)
  - [DeepOps Deployment Validation](#deepops-deployment-validation)

## Linting

`scripts/push.sh` pushes a checkout's commits to origin's main the safe way: it
rebases onto origin/main, runs the full check and pushes, holding a lock in the clone's
shared git directory throughout, so worktrees of one clone push one at a time and each
checks on top of what the one before pushed (without it, a ten-minute check ended in a
refused push whenever another worktree pushed meanwhile).

`scripts/check.sh` runs every repository check: the style normalizer, generated docs,
yamllint, `ansible-lint` with the project [`.ansible-lint`](../../.ansible-lint)
configuration, playbook syntax checks and the unit suites. The few tools it runs from
outside `.venv` (Lua 5.4, Go) are listed in it once: `scripts/setup.sh` and CI install the
missing ones (`scripts/check.sh --missing-tools`). CI runs it in
[checks.yml](../../.github/workflows/checks.yml), and the pre-commit hook runs
`scripts/check.sh --fast` on each commit. The hook
([`scripts/deepops/pre-commit.sh`](../../scripts/deepops/pre-commit.sh)) runs the
checks on a copy of what is staged, in a throwaway worktree: the working tree is never
stashed or touched (a shared clone's other edits stay put), nothing untracked in it
counts, and a fix a hook would make is shown for you to apply. To skip roles that have known issues or are
still in development, list them under `exclude_paths` in `.ansible-lint`.

### Secrets and site data

[`scripts/deepops/sensitive_guard.py`](../../scripts/deepops/sensitive_guard.py) keeps
secrets and site-specific facts out of history. `scripts/setup.sh` wires it into
three hooks: staged lines (pre-commit), the commit
message (commit-msg) and every commit a push would publish (pre-push).

- Built-in rules catch generic secret formats (private keys, Slack, GitHub and AWS
  tokens, webhooks).
- Site rules live in the site's private config repository, `config/sensitive-patterns`
  (copy [`config.example/sensitive-patterns`](../../config.example/sensitive-patterns)),
  so the list itself is never published. Each rule names its generic replacement.
- Findings print the place and the rule, never the matched text. Mark a deliberate
  line with `sensitive-guard: allow`.

```bash
scripts/deepops/sensitive_guard.py tree                       # every tracked file
scripts/deepops/sensitive_guard.py history main ^upstream/master   # our commits
scripts/deepops/sensitive_guard.py replace-text > rules.txt   # git filter-repo --replace-text input
```

If something did reach a pushed commit, rewrite only your own commits with
`git filter-repo --replace-text rules.txt --refs <first-bad>^..main` (the range keeps
upstream commits and their signatures intact), force-push with lease, and ask GitHub
Support to purge cached views of the old commits.

### Silent reverts

Several people pushing to one branch meet a quiet failure: a commit built on an
out-of-date copy of a file, committed on top of newer history, puts the file back to an
older version and undoes the commits in between without anyone saying so.
[`scripts/deepops/no_silent_revert.py`](../../scripts/deepops/no_silent_revert.py), a
pre-push hook, refuses such a commit and names the commits it would undo. A commit that
means it names a commit it undoes in its message (as `git revert` writes "This reverts
commit <hash>.") and passes; merges are checked against their first parent. Rebase onto the
remote and redo the change otherwise. Both push guards run from git's own pre-push hook,
[`scripts/deepops/pre-push.sh`](../../scripts/deepops/pre-push.sh), and check exactly
the commits each pushed ref sends, a detached HEAD's too
([`scripts/deepops/push_range.py`](../../scripts/deepops/push_range.py)); pre-commit
would hand them only the first ref. `scripts/setup.sh` installs the hooks from the
main worktree only: they are shared by every worktree of the clone, and the site rules
(`config/sensitive-patterns`) are read from the main worktree too.

```bash
scripts/deepops/no_silent_revert.py range origin/main..HEAD   # before pushing by hand
```

The same undoing starts locally when one clone has several worktrees: a branch checked
out in one of them, moved from another (`checkout -B`, `branch -f`, `update-ref`),
leaves the first worktree's index and files at the old commit, so its next commit
reverts everything in between. git 2.44 refuses `checkout -B` there; older git does not.
[`scripts/deepops/branch-guard.sh`](../../scripts/deepops/branch-guard.sh), the
reference-transaction hook `scripts/setup.sh` installs in this repository and in the
site config's, refuses any such move: a branch moves only from the worktree that has it
checked out. Work in another worktree on a detached HEAD or a branch of its own.

## DeepOps end-to-end testing

Public DeepOps pull requests are validated with GitHub Actions for setup, linting, CodeQL, and every role's Molecule scenarios. Those checks catch many packaging and role-regression issues, but they do not replace deployment validation on real GPU systems.

DeepOps also retains a legacy Jenkins/Vagrant test harness in the [jenkins](../../workloads/jenkins) and [virtual](../../virtual) directories. Treat those files as community-supported reference material unless maintainers explicitly say a Jenkins job is still authoritative. New release validation should record the exact environment, operating system, GPU stack, and workload checks used for the pull request or release.

The goal of DeepOps validation is to prevent bugs from being introduced into the code base and to identify when changes in third-party platforms have affected the DeepOps deployment mechanisms. In general, Kubernetes and Slurm deployment issues are detected and resolved with urgency. Many components of DeepOps are third-party open source tools that may silently fail or change without notice. The team will make a best-effort to resolve these issues and include regression tests, however there may be times where a fix is unavailable. Historically, this has been an issue with Rook-Ceph and Kubeflow, and those GitHub communities are best equipped to help with resolutions.

### Testing Method

DeepOps currently uses these testing layers:

- GitHub Actions for public pull request checks, including setup, linting, CodeQL, and every role's Molecule scenarios.
- Focused local validation for changed playbooks, roles, scripts, and documentation before opening or updating a pull request.
- GPU-backed deployment validation for changes that affect Slurm, Kubernetes, drivers, container runtimes, DGX platform software, or workload examples.
- Legacy Jenkins/Vagrant jobs as reference material for operators who still run that harness.

If a change requires GPU-backed validation, document the validation environment and results in the pull request. If that validation cannot be run, state the gap explicitly instead of relying on the legacy Jenkins matrix.

### Tests

A short description of the historical Jenkins test matrix is outlined below. The full suite of legacy jobs can be reviewed in the [jenkins](../../workloads/jenkins) directory. These rows are not a promise of current public CI coverage; check the pull request's GitHub Actions and validation notes for current status.

**Validation Matrix**

| Test                                                | [PR](../../workloads/jenkins/Jenkinsfile) | [Nightly](../../workloads/jenkins/Jenkinsfile-nightly) | [Nightly Multi-node](../../workloads/jenkins/Jenkinsfile-multi-nightly) | Comments                             |
| --------------------------------------------------- | ----------------------------------------- | ------------------------------------------------------ | ----------------------------------------------------------------------- | ------------------------------------ |
| Ubuntu 18.04                                        | x                                         | x                                                      | x                                                                       | Legacy Jenkins/Vagrant reference only |
| Ubuntu 20.04                                        |                                           | x                                                      | x                                                                       | Legacy Jenkins/Vagrant reference only |
| Ubuntu 22.04                                        |                                           |                                                        |                                                                         | setup.sh GitHub Action               |
| Ubuntu 24.04                                        |                                           |                                                        |                                                                         | setup.sh and Molecule GitHub Actions |
| CentOS 7                                            |                                           | x                                                      | x                                                                       | Legacy Jenkins/Vagrant reference only |
| CentOS 8                                            |                                           |                                                        | x                                                                       | Legacy Jenkins/Vagrant reference only |
| DGX OS                                              |                                           |                                                        |                                                                         | Syntax-checked only; full validation requires DGX hardware |
| RHEL                                                |                                           |                                                        |                                                                         | DGX software-stack role syntax-checked only; full validation requires DGX hardware and subscriptions |
| 1 mgmt node                                         | x                                         | x                                                      |                                                                         |                                      |
| 3 mgmt nodes                                        |                                           |                                                        | x                                                                       |                                      |
| 1 gpu node                                          | x                                         | x                                                      |                                                                         |                                      |
| 2 gpu nodes                                         |                                           |                                                        | x                                                                       |                                      |
| Deploys Slurm                                       | x                                         | x                                                      | x                                                                       |                                      |
| Verify GPU workload with srun                       | x                                         | x                                                      | x                                                                       |
| Verify Slurm nfs mount                              | x                                         | x                                                      | x                                                                       |                                      |
| Verify basic mpi job in Slurm                       | x                                         | x                                                      | x                                                                       |                                      |
| Verify basic enroot job in Slurm                    | x                                         | x                                                      | x                                                                       | x                                    |
| Verify rsyslog setup in Slurm                       | x                                         | x                                                      | x                                                                       |                                      |
| Verify rsyslog setup in K8s                         | x                                         | x                                                      | x                                                                       |                                      |
| Deploys K8s (No GPU Operator)                       | x                                         | x                                                      | x                                                                       |                                      |
| Deploy & validate K8s (GPU Operator)                |                                           | x                                                      | x                                                                       |                                      |
| Verify Device Plugin is working                     | x                                         | x                                                      | x                                                                       |
| Verify GPU Feature Discovery is labeling nodes      | x                                         | x                                                      | x                                                                       |
| Verify GPU workload in K8s                          | x                                         | x                                                      | x                                                                       |
| Verifies Ingress configuration                      | x                                         | x                                                      |
| Verifies local Docker registry                      | x                                         | x                                                      | x                                                                       |
| Verifies local Docker mirror                        | x                                         | x                                                      | x                                                                       |
| Verify Grafana loads (no metric verification        | x                                         | x                                                      | x                                                                       |
| Verify GPU dashboard loads                          | x                                         | x                                                      | x                                                                       |
| Test Kubeflow deployment (with Dex)                 |                                           | x                                                      | x                                                                       |
| Test Kubeflow deployment (without Dex)              |                                           | x                                                      | x                                                                       |
| Execute GPU workload with Kubeflow pipeline         |                                           | x                                                      | x                                                                       |
| Verify GPU dashboard metrics are configured in DCGM | x                                         | x                                                      | x                                                                       | x                                    |
| Airgap testing                                      |                                           |                                                        |                                                                         | No testing support                   |
| Verify multinode job in K8s                         |                                           |                                                        |                                                                         | No testing support                   |
| Verify Ceph deployment                              |                                           |                                                        |                                                                         | Support dropped                      |
| MAAS Deployment                                     |                                           |                                                        |                                                                         | Used regularly, no automated testing |
| MIG configuration                                   |                                           |                                                        |                                                                         | No testing support                   |

## DeepOps Ansible role testing

Roles can carry [Ansible Molecule](https://molecule.readthedocs.io/en/latest/) scenarios,
which run the role alone in a container and catch what the end-to-end tests do not.
Every role with a scenario (`roles/<role>/molecule/<scenario>/molecule.yml`) is tested
for each pull request: the CI matrix is `scripts/deepops/molecule.py list`, never a hand
list.

Molecule runs the role in a Docker container with systemd, so services start and
restart as on a node. The container is never privileged and gets no host devices: it
is a node without special hardware, so a role there can never configure the Docker
host's own (a privileged run once set the host BMC's address). Roles that need
specific hardware (GPUs, a BMC), reboot, or change the kernel test only the parts a
container allows: a component's absent path, and the refusal where the hardware or a
site input is missing (see the [bmc](../../roles/bmc/molecule/default/verify.yml)
scenario). A role that starts containers of its own (the Slurm package build) has no
scenario: Docker inside the container needs the privilege it never gets, and
`validate_slurm.py` on a cluster is its test.

### Running the scenarios

From the repository root, after `scripts/setup.sh` (Docker, `.venv`, Galaxy content):

```bash
uv run scripts/deepops/molecule.py test              # every role
uv run scripts/deepops/molecule.py test sshd nhc     # some roles
```

Each role's containers are named after the role, so two roles can run at once on one
Docker host.

### Defining Molecule tests for a role

Everything scenarios share is in [`.config/molecule/config.yml`](../../.config/molecule/config.yml),
which Molecule merges under every scenario: the platform (Ubuntu 24.04 with systemd,
pinned by digest), the driver, and the repository's Ansible paths (Galaxy roles and
collections, `plugins/module_utils`, `plugins/filter`). A scenario states only what it
adds:

1. Create `roles/<role>/molecule/default/` with `converge.yml` (the play that applies
   the role) and, ideally, `verify.yml` (checks that it did, see
   [default_target](../../roles/default_target/molecule/default/verify.yml)) and
   `prepare.yml` (anything the container lacks that a node has).
2. Write `molecule.yml` with only the scenario's own settings: inventory vars
   (`provisioner.inventory.group_vars`), groups (`provisioner.inventory.hosts`), a test
   sequence. With nothing to add it reads `{}`. Never restate `platforms`: a list in a
   scenario replaces the shared one.
3. A component's removal is a scenario of its own (`molecule/absent/`): present, then
   absent, then a rerun that changes nothing.
4. Run `uv run scripts/deepops/molecule.py test <role>`; CI picks the role up by itself.

## DeepOps Deployment Validation

The Slurm and Kubernetes deployment guides both document cluster verification steps. These should be run during the installation process to validate a GPU workload can be executed on the cluster.

Additional services such as Kubeflow or Monitoring may have additional validation steps that are documented in the corresponding DeepOps READMEs and the official documentation.

For workloads that can be used as post-deployment validation, see the example workloads for [k8s](../../workloads/examples/k8s/) and [slurm](../../workloads/examples/slurm).
