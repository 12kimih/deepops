# Testing

DeepOps Testing, CI/CD, and Validation

- [Testing](#testing)
  - [Linting](#linting)
  - [DeepOps end-to-end testing](#deepops-end-to-end-testing)
    - [Testing Method](#testing-method)
    - [Tests](#tests)
  - [DeepOps Ansible role testing](#deepops-ansible-role-testing)
    - [Defining Molecule tests for a new role](#defining-molecule-tests-for-a-new-role)
  - [DeepOps Deployment Validation](#deepops-deployment-validation)

## Linting

`ansible-lint` is automatically run for each role in the `roles/` directory using a [Github action](../../.github/workflows/ansible-lint-roles.yml).
This action runs `ansible-lint` once over the `roles/` directory with the project [`.ansible-lint`](../../.ansible-lint) configuration.
If the Github action reports success, all roles should have passed linting.

The linting process can also be executed manually in a checkout of the DeepOps repo,
by running `./scripts/deepops/ansible-lint-roles.sh`.

To skip roles that have known issues or are still in development, list them under
`exclude_paths` in `.ansible-lint`. (The workflow still sets an `ANSIBLE_LINT_EXCLUDE`
variable, but nothing reads it.)

## DeepOps end-to-end testing

Public DeepOps pull requests are validated with GitHub Actions for setup, linting, CodeQL, and selected Molecule role tests. Those checks catch many packaging and role-regression issues, but they do not replace deployment validation on real GPU systems.

DeepOps also retains a legacy Jenkins/Vagrant test harness in the [jenkins](../../workloads/jenkins) and [virtual](../../virtual) directories. Treat those files as community-supported reference material unless maintainers explicitly say a Jenkins job is still authoritative. New release validation should record the exact environment, operating system, GPU stack, and workload checks used for the pull request or release.

The goal of DeepOps validation is to prevent bugs from being introduced into the code base and to identify when changes in third-party platforms have affected the DeepOps deployment mechanisms. In general, Kubernetes and Slurm deployment issues are detected and resolved with urgency. Many components of DeepOps are third-party open source tools that may silently fail or change without notice. The team will make a best-effort to resolve these issues and include regression tests, however there may be times where a fix is unavailable. Historically, this has been an issue with Rook-Ceph and Kubeflow, and those GitHub communities are best equipped to help with resolutions.

### Testing Method

DeepOps currently uses these testing layers:

- GitHub Actions for public pull request checks, including setup, linting, CodeQL, and selected Molecule role tests.
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
| Ubuntu 22.04                                        |                                           |                                                        |                                                                         | setup.sh and Molecule GitHub Actions |
| Ubuntu 24.04                                        |                                           |                                                        |                                                                         | setup.sh GitHub Action              |
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

A subset of the Ansible roles in DeepOps have tests defined using [Ansible Molecule](https://molecule.readthedocs.io/en/latest/).
This testing mechanism allows the roles to be tested individually, providing additional test signal to identify issues which do not appear in the end-to-end tests.
The roles listed in [`.github/workflows/molecule.yml`](../../.github/workflows/molecule.yml) are tested automatically for each pull request.

Molecule testing runs the Ansible role in question inside a Docker container.
As such, not all roles will be easy to test with this mechanism.
Roles which mostly involve installing software, configuring services, or executing scripts should generally be possible to test.
Roles which rely on the presence of specific hardware (such as GPUs), which reboot the nodes they act on, or which make changes to kernel configuration are going to be harder to test with Molecule.

### Defining Molecule tests for a new role

To add Molecule tests to a new role, the following procedure can be used.

1. Ensure you have Docker installed in your development environment

2. Install Ansible Molecule and `community.docker` Ansible Galaxy collection in your development environment

```
$ python3 -m pip install "molecule[docker,lint]"
$ ansible-galaxy collection install community.docker
```

3. Initialize Molecule in your new role

```bash
cd deepops/roles/<your-role>
molecule init scenario -r <your-role> --driver-name docker
```

4. In the file `molecule/default/molecule.yml`, define the list of platforms to be tested.
   The existing scenarios use Ubuntu 22.04; the setup workflow runs on Ubuntu 22.04 and 24.04.
   Add Red Hat family images only for roles that explicitly support them, and validate the image choice for that role.
   Keep Ubuntu 18.04, Ubuntu 20.04, CentOS 7, and CentOS 8 scenarios in separately named legacy test scenarios when maintaining older compatibility paths.
   To test the current Ubuntu stacks, the following `platforms` stanza can be used.

```yaml
platforms:
  - name: ubuntu-2204
    image: geerlingguy/docker-ubuntu2204-ansible
    pre_build_image: true
  - name: ubuntu-2404
    image: geerlingguy/docker-ubuntu2404-ansible
    pre_build_image: true
```

5. If you haven't already, define your role's metadata in the file `meta/main.yml`.
   A sample `meta.yml` is shown here:

```yaml
galaxy_info:
  role_name: <your-role>
  namespace: deepops
  author: DeepOps Team
  company: NVIDIA
  description: <your-description>
  license: 3-Clause BSD
  min_ansible_version: 2.9
```

6. Once this is done, verify that your role executes successfully in the Molecule environment by running `molecule test`. If you run into any issues, consult the [Molecule documentation](https://molecule.readthedocs.io/en/latest/index.html) for help resolving them.

7. (optional) In addition to testing successful execution, you can add additional tests which will be run after your role completes in a file `molecule/default/verify.yml`. This is an Ansible playbook that will run in the same environment as your playbook ran. For a simple example of such a verify playbook, see the [default_target role](../../roles/default_target/molecule/default/verify.yml).

8. Once you're confident that your new tests are all passing, add your role to the `deepops-role` section in the `.github/workflows/molecule.yml` file.

## DeepOps Deployment Validation

The Slurm and Kubernetes deployment guides both document cluster verification steps. These should be run during the installation process to validate a GPU workload can be executed on the cluster.

Additional services such as Kubeflow, Open OnDemand, or Monitoring may have additional validation steps that are documented in the corresponding DeepOps READMEs and the official documentation.

For workloads that can be used as post-deployment validation, see the example workloads for [k8s](../../workloads/examples/k8s/) and [slurm](../../workloads/examples/slurm).
