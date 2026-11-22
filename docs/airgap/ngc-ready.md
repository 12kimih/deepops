# NGC Ready

Deploying the NGC-Ready playbook offline

- [NGC Ready](#ngc-ready)
  - [Necessary software mirrors](#necessary-software-mirrors)
    - [Ubuntu](#ubuntu)
    - [Enterprise Linux](#enterprise-linux)
  - [Configuring DeepOps](#configuring-deepops)
    - [Configure servers to use your mirrors for the Linux distribution package repositories](#configure-servers-to-use-your-mirrors-for-the-linux-distribution-package-repositories)
    - [Configure DeepOps to use your mirrors for non-distribution package repositories](#configure-deepops-to-use-your-mirrors-for-non-distribution-package-repositories)
    - [Configure DeepOps to use your mirrors for HTTP downloads](#configure-deepops-to-use-your-mirrors-for-http-downloads)
    - [Configure DeepOps to use your mirrors for container image pulls](#configure-deepops-to-use-your-mirrors-for-container-image-pulls)
  - [Running the NGC-Ready playbook](#running-the-ngc-ready-playbook)

## Necessary software mirrors

Deploying the NGC-Ready playbook assumes that several package repositories and individual software packages are available to install.
In order to deploy this configuration without Internet access, you will need to have the following software available in offline mirrors.

### Ubuntu

The following Apt repositories will need to be mirrored in the offline environment:

- Ubuntu distribution repositories (these also provide the NVIDIA driver packages)
- Docker CE repository
- NVIDIA Container Toolkit repository
- NVIDIA CUDA repository (needed for DCGM, or with `nvidia_driver_install_method: nvidia_repo`)

For instructions on mirroring these repositories, see the [doc on Apt mirrors](./mirror-apt-repos.md).

The `cuda-keyring` package comes from the mirrored CUDA repository itself (`package_repos_cuda_base`); it needs no separate HTTP mirror.

Container images are only needed if you want to run the tests built into the playbook:

`ngc_ready_cuda_container`, `ngc_ready_pytorch_container` and
`ngc_ready_tensorflow_container`, each pinned by tag and digest in
[versions.md](../deepops/versions.md).

For instructions on setting up a Docker registry mirror, see the [doc on Docker mirrors](./mirror-docker-images.md).

### Enterprise Linux

The following RPM repositories will need to be mirrored in the offline environment:

- Enterprise Linux distribution repositories (RHEL, Rocky Linux or AlmaLinux, depending on your distro)
- Docker CE repository
- NVIDIA Container Toolkit repository
- NVIDIA CUDA repository (NVIDIA driver and DCGM)

For instructions on mirroring these repositories, see the [doc on RPM mirrors](./mirror-rpm-repos.md).

The following files may need to be downloaded and made available in an HTTP mirror:

- EPEL package (found [here](https://fedoraproject.org/wiki/EPEL)), on Red Hat Enterprise Linux only: Rocky and Alma Linux install `epel-release` from their own extras repository

For instructions on setting up an HTTP mirror, see the [doc on HTTP mirrors](./mirror-http-files.md).

Container images (how to mirror) are only needed if you want to run the tests built into the playbook:

`ngc_ready_cuda_container`, `ngc_ready_pytorch_container` and
`ngc_ready_tensorflow_container`, each pinned by tag and digest in
[versions.md](../deepops/versions.md).

For instructions on setting up a Docker registry mirror, see the [doc on Docker mirrors](./mirror-docker-images.md).

## Configuring DeepOps

To deploy the NGC-Ready playbook offline, you will need to configure your servers and DeepOps to make use of your mirrors.

### Configure servers to use your mirrors for the Linux distribution package repositories

On Ubuntu, the cluster deploys point the distribution archive at your mirror (`package_repos_ubuntu_mirror`, applied by `playbooks/generic/os-updates.yml`); this NGC-ready playbook does not import it, so run that playbook first or configure the archive yourself. On Red Hat family systems, configure your servers to use your offline distribution mirrors directly.

On Ubuntu 24.04 servers, you should edit `/etc/apt/sources.list.d/ubuntu.sources` (`/etc/apt/sources.list` on older releases) to replace references to the Ubuntu distribution servers with your own mirror.
For example,

_Replace this..._

```bash
URIs: http://archive.ubuntu.com/ubuntu/
```

_With this..._

```bash
URIs: http://<your-mirror-server>/ubuntu/
```

On Enterprise Linux servers, you should edit the appropriate repo files in `/etc/yum.repos.d` and replace references to the upstream distribution servers with your own mirror.
For repositories that reference a `mirrorlist`, you should replace these with `baseurl` parameters.

For example,

_Replace this..._

```
[baseos]
name=Rocky Linux $releasever - BaseOS
mirrorlist=https://mirrors.rockylinux.org/mirrorlist?arch=$basearch&repo=BaseOS-$releasever
#baseurl=http://dl.rockylinux.org/$contentdir/$releasever/BaseOS/$basearch/os/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-Rocky-9
```

_With this..._

```bash
[baseos]
name=Rocky Linux $releasever - BaseOS
baseurl=http://<your-mirror-server>/rocky/$releasever/BaseOS/$basearch/os/
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-Rocky-9
```

In all cases, you should edit the URLs appropriately to ensure they can download from the paths exported from your mirrors.

### Configure DeepOps to use your mirrors for non-distribution package repositories

The NGC-Ready playbook depends on the Docker CE and NVIDIA Container Toolkit package repositories.
DeepOps sets up these repositories automatically during the installation.

To configure alternate URLs for these repositories, set the following variables in your DeepOps configuration:

**Ubuntu**

```yaml
nvidia_container_toolkit_repo_base_url: "http://<your-package-mirror>/<your-path-to-libnvidia-container>"
nvidia_container_toolkit_repo_gpg_url: "http://<your-package-mirror>/<your-path-to-libnvidia-container-gpgkey>"
```

The Docker repository is a mirror of `https://download.docker.com/linux/ubuntu` (Kubespray reads the same settings):

```yaml
docker_ubuntu_repo_base_url: "http://<your-package-mirror>/<your-path-to-docker-ce-ubuntu>"
docker_ubuntu_repo_gpgkey: "http://<your-package-mirror>/<your-path-to-docker-ce-ubuntu>/gpg"
```

**Enterprise Linux**

```yaml
# Mirrors of https://download.docker.com/linux/rhel and https://nvidia.github.io/libnvidia-container
docker_rh_repo_base_url: "http://<your-package-mirror>/<your-path-to-docker-ce>"
docker_rh_repo_gpgkey: "http://<your-package-mirror>/<your-path-to-docker-ce>/gpg"
nvidia_container_toolkit_repo_base_url: "http://<your-package-mirror>/<your-path-to-libnvidia-container>"
```

### Configure DeepOps to use your mirrors for HTTP downloads

Current NVIDIA Container Toolkit installs do not need a standalone `nvidia-docker` wrapper file.
Use this section only for other direct HTTP downloads required by the roles you enable.

The third-party package repositories come from one role, `roles/package_repos`.
Point it at your mirrors; the layout below each base URL stays the upstream one.

```yaml
# NVIDIA CUDA repository (drivers, CUDA, DCGM): the role installs the cuda-keyring
# from here and points the APT source (or the dnf repository) at the same URL.
package_repos_cuda_base: "http://<your-package-mirror>/<your-path>/compute/cuda/repos"

# EPEL, Enterprise Linux only
package_repos_epel_release: "http://<your-http-mirror>/<your-path>/epel-release.rpm"
package_repos_epel_key: "http://<your-http-mirror>/<your-path>/RPM-GPG-KEY-EPEL-9"
```

The keyring is checked against `package_repos_cuda_keyring_checksums`, so a mirror
must serve NVIDIA's file unchanged.

### Configure DeepOps to use your mirrors for container image pulls

If running the container tests as part of the NGC-Ready playbook, set the following variables in your DeepOps configuration:

```yaml
ngc_ready_cuda_container: "<your-container-registry>/nvidia/cuda:<tag>@sha256:<digest>"
ngc_ready_pytorch_container: "<your-container-registry>/nvidia/pytorch:<tag>@sha256:<digest>"
ngc_ready_tensorflow_container: "<your-container-registry>/nvidia/tensorflow:<tag>@sha256:<digest>"
```

Keep each default's tag and digest: a mirror made as in the Docker mirrors doc keeps
the digest, so the pin still proves the image is the one reviewed.

## Running the NGC-Ready playbook

After setting these variables to point to your local mirrors, you should be able to run the NGC-Ready playbook:

```bash
ansible-playbook playbooks/ngc-ready-server.yml
```
