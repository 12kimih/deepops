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

The following files may need to be downloaded and made available in an HTTP mirror:

- The `cuda-keyring` package (only when using the CUDA repository)

For instructions on setting up an HTTP mirror, see the [doc on HTTP mirrors](./mirror-http-files.md).

Container images are only needed if you want to run the tests built into the playbook:

- nvcr.io/nvidia/cuda:13.2.1-base-ubuntu24.04
- nvcr.io/nvidia/pytorch:26.05-py3
- nvcr.io/nvidia/tensorflow:25.02-tf2-py3

For instructions on setting up a Docker registry mirror, see the [doc on Docker mirrors](./mirror-docker-images.md).

### Enterprise Linux

The following RPM repositories will need to be mirrored in the offline environment:

- Enterprise Linux distribution repositories (RHEL, Rocky Linux or AlmaLinux, depending on your distro)
- Docker CE repository
- NVIDIA Container Toolkit repository
- NVIDIA CUDA repository (NVIDIA driver and DCGM)

For instructions on mirroring these repositories, see the [doc on RPM mirrors](./mirror-rpm-repos.md).

The following files may need to be downloaded and made available in an HTTP mirror:

- EPEL package (found [here](https://fedoraproject.org/wiki/EPEL))

For instructions on setting up an HTTP mirror, see the [doc on HTTP mirrors](./mirror-http-files.md).

Container images (how to mirror) are only needed if you want to run the tests built into the playbook:

- nvcr.io/nvidia/cuda:13.2.1-base-ubuntu24.04
- nvcr.io/nvidia/pytorch:26.05-py3
- nvcr.io/nvidia/tensorflow:25.02-tf2-py3

For instructions on setting up a Docker registry mirror, see the [doc on Docker mirrors](./mirror-docker-images.md).

## Configuring DeepOps

To deploy the NGC-Ready playbook offline, you will need to configure your servers and DeepOps to make use of your mirrors.

### Configure servers to use your mirrors for the Linux distribution package repositories

DeepOps does not configure the location of your Linux distribution's package repositories (e.g., Ubuntu or CentOS repositories).
Instead, you will need to configure your servers to use your offline package mirrors directly.

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

```bash
nvidia_container_toolkit_repo_base_url: "http://<your-package-mirror>/<your-path-to-libnvidia-container>"
nvidia_container_toolkit_repo_gpg_url: "http://<your-package-mirror>/<your-path-to-libnvidia-container-gpgkey>"
```

The Docker role has no variable for the Ubuntu repository: it always adds `https://download.docker.com/linux/ubuntu` (and its `/gpg` key) to `/etc/apt/sources.list.d/docker.sources`.
Make that host name resolve to your mirror, or install Docker yourself and set `docker_install: false`.

**Enterprise Linux**

```bash
# A .repo file whose baseurl points at your Docker CE mirror
docker_rh_repo_url: "http://<your-package-mirror>/<your-path-to-docker-ce.repo>"

nvidia_container_toolkit_rpm_repo_url: "http://<your-package-mirror>/<your-path-to-nvidia-container-toolkit.repo>"
```

### Configure DeepOps to use your mirrors for HTTP downloads

Current NVIDIA Container Toolkit installs do not need a standalone `nvidia-docker` wrapper file.
Use this section only for other direct HTTP downloads required by the roles you enable.

If installing on Enterprise Linux, you will need to provide a URL for the EPEL package.
For example,

```bash
epel_package: "http://<your-http-mirror>/<your-path>/epel-release.rpm"
```

NVIDIA DCGM installs from the CUDA repository.

**Ubuntu**

```bash
nvidia_driver_ubuntu_cuda_keyring_url: "http://<your-http-mirror>/<your-path>/cuda-keyring_1.1-1_all.deb"
```

The `cuda-keyring` package also adds an APT source pointing at `developer.download.nvidia.com`; replace it with your CUDA mirror on the offline hosts.

**Enterprise Linux**

```bash
nvidia_driver_rhel_cuda_repo_baseurl: "http://<your-package-mirror>/<your-path-to-cuda-repo>/"
nvidia_driver_rhel_cuda_repo_gpgkey: "http://<your-package-mirror>/<your-path-to-cuda-repo>/D42D0685.pub"
```

### Configure DeepOps to use your mirrors for container image pulls

If running the container tests as part of the NGC-Ready playbook, set the following variables in your DeepOps configuration:

```bash
ngc_ready_cuda_container: "<your-container-registry>/nvidia/cuda:13.2.1-base-ubuntu24.04"
ngc_ready_pytorch: "<your-container-registry>/nvidia/pytorch:26.05-py3"
ngc_ready_tensorflow: "<your-container-registry>/nvidia/tensorflow:25.02-tf2-py3"
```

## Running the NGC-Ready playbook

After setting these variables to point to your local mirrors, you should be able to run the NGC-Ready playbook:

```bash
ansible-playbook playbooks/ngc-ready-server.yml
```
