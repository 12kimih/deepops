# Mirror Apt Repos

Set up offline repository mirrors for APT

## Table of Contents

- [Mirror Apt Repos](#mirror-apt-repos)
  - [Table of Contents](#table-of-contents)
  - [Introduction](#introduction)
  - [Identifying package repositories to mirror](#identifying-package-repositories-to-mirror)
    - [Ubuntu repositories](#ubuntu-repositories)
    - [Docker repository](#docker-repository)
      - [APT Configuration](#apt-configuration)
      - [GPG Key Validation](#gpg-key-validation)
    - [NVIDIA CUDA repository](#nvidia-cuda-repository)
      - [APT Configuration](#apt-configuration-1)
      - [GPG Key Validation](#gpg-key-validation-1)
    - [NVIDIA Container Toolkit](#nvidia-container-toolkit)
      - [APT Configuration](#apt-configuration-2)
      - [GPG Key Validation](#gpg-key-validation-2)
    - [Additional DEB packages](#additional-deb-packages)
  - [Downloading package repositories on a machine with Internet access](#downloading-package-repositories-on-a-machine-with-internet-access)
  - [Transferring repositories to offline network](#transferring-repositories-to-offline-network)
  - [Create mirrors on offline network](#create-mirrors-on-offline-network)

## Introduction

Most of the software necessary to run GPU-enabled applications on Ubuntu servers is available via APT repositories.
In order to deploy this software in an offline environment, the most straightforward path is to mirror the repositories in your offline network.

## Identifying package repositories to mirror

In order to mirror package repositories to use offline, you must first identify which repositories contain the software you need.
For APT repositories, this means identifying the appropriate lines in the `/etc/apt/sources.list` or `/etc/apt/sources.list.d/` configuration.

If you have a test system in an environment with Internet access, you can check which APT sources are in use with the following process:

```bash
cd /etc/apt
grep http sources.list
grep -R http sources.list.d/
```

The rest of this section lists the configuration for common repositories used by DeepOps.

### Ubuntu repositories

DeepOps assumes that a full mirror of your Ubuntu distribution repositories will be available for package installs.
If you do not already have these distribution repositories available, the `sources.list` configuration should be:

```bash
deb http://security.ubuntu.com/ubuntu <release-name>-security main
deb http://security.ubuntu.com/ubuntu <release-name>-security universe
deb http://security.ubuntu.com/ubuntu <release-name>-security multiverse
deb http://archive.ubuntu.com/ubuntu/ <release-name> main multiverse universe
deb http://archive.ubuntu.com/ubuntu/ <release-name>-updates main multiverse universe
```

where `<release-name>` is the name of the Ubuntu release you want to mirror.
This is `bionic` for Ubuntu 18.04, `focal` for Ubuntu 20.04, `jammy` for Ubuntu 22.04, and `noble` for Ubuntu 24.04.

### Docker repository

#### APT Configuration

```bash
deb https://download.docker.com/linux/ubuntu <release-name> stable
```

#### GPG Key Validation

```bash
https://download.docker.com/linux/ubuntu/gpg
```

where `<release-name>` is the name of the Ubuntu release you want to mirror.
This is `bionic` for Ubuntu 18.04, `focal` for Ubuntu 20.04, `jammy` for Ubuntu 22.04, and `noble` for Ubuntu 24.04.

### NVIDIA CUDA repository

#### APT Configuration

**Ubuntu 22.04**

```bash
deb https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64 /
```

**Ubuntu 24.04**

```bash
deb https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64 /
```

#### GPG Key Validation

**Ubuntu 22.04**

```bash
https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/3bf863cc.pub
```

**Ubuntu 24.04**

```bash
https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/3bf863cc.pub
```

### NVIDIA Container Toolkit

#### APT Configuration

```bash
deb https://nvidia.github.io/libnvidia-container/stable/deb/$(ARCH) /
```

#### GPG Key Validation

```bash
https://nvidia.github.io/libnvidia-container/gpgkey
```

### Additional DEB packages

The following DEB files are not installed from an APT repository, but installed ad-hoc from direct URLs or file paths.

- _CUDA keyring_: `cuda-keyring_1.1-1_all.deb` comes from the mirrored CUDA repository itself (`package_repos_cuda_base`), installed by `roles/package_repos` wherever a role needs the CUDA repository (DCGM, the CUDA toolkit, the driver with `nvidia_driver_install_method: nvidia_repo`) and checked against `package_repos_cuda_keyring_checksums`: no separate download. On Ubuntu that keyring is what apt trusts; the `3bf863cc.pub` keys above are only for checking the mirror by hand
- _NVIDIA Enroot_: Download from [Enroot releases](https://github.com/NVIDIA/enroot/releases/)

## Downloading package repositories on a machine with Internet access

On an Ubuntu machine with Internet access, install the `apt-mirror` package:

```bash
sudo apt update
sudo apt install apt-mirror
```

The package in the repository is not maintained anymore and has some issues. There are community supported forks. Get updated version of the `apt-mirror`:
```
git clone https://github.com/Stifler6996/apt-mirror
cd apt-mirror
sudo cp -f apt-mirror /usr/bin/
```

After installing `apt-mirror`, edit the `/etc/apt/mirror.list` file make the following changes:

- Set the `base_path` to the desired download path for your mirror (here, `/var/repos`)
- Add a list of APT configuration lines for each repo you wish to mirror

For example, if we just want to mirror the Docker and NVIDIA Container Toolkit repositories, this configuration would work:

```
############# config ##################
#
set base_path    /var/repos
set nthreads     20
set _tilde 0
#
############# end config ##############

deb https://download.docker.com/linux/ubuntu noble stable
deb https://nvidia.github.io/libnvidia-container/stable/deb/amd64 /
```

The full mirror.list file for DeepOps:

```
############# config ##################
#
set base_path    /var/repos
#
# set mirror_path  $base_path/mirror
# set skel_path    $base_path/skel
# set var_path     $base_path/var
# set cleanscript $var_path/clean.sh
# set defaultarch  <running host architecture>
# set postmirror_script $var_path/postmirror.sh
# set run_postmirror 0
set nthreads     20
set _tilde 0
#
############# end config ##############

deb http://archive.ubuntu.com/ubuntu noble main restricted universe multiverse
deb http://archive.ubuntu.com/ubuntu noble-security main restricted universe multiverse
deb http://archive.ubuntu.com/ubuntu noble-updates main restricted universe multiverse
deb http://archive.ubuntu.com/ubuntu noble-proposed main restricted universe multiverse
deb http://archive.ubuntu.com/ubuntu noble-backports main restricted universe multiverse
deb http://ppa.launchpad.net/maas/3.7/ubuntu noble main
deb http://archive.canonical.com/ubuntu noble partner

deb-src http://archive.ubuntu.com/ubuntu noble main restricted universe multiverse
deb-src http://archive.ubuntu.com/ubuntu noble-security main restricted universe multiverse
deb-src http://archive.ubuntu.com/ubuntu noble-updates main restricted universe multiverse
deb-src http://archive.ubuntu.com/ubuntu noble-proposed main restricted universe multiverse
deb-src http://archive.ubuntu.com/ubuntu noble-backports main restricted universe multiverse

deb https://download.docker.com/linux/ubuntu noble stable
deb https://nvidia.github.io/libnvidia-container/stable/deb/amd64 /

deb https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64 /

deb http://repo.download.nvidia.com/baseos/ubuntu/noble/x86_64/ noble common dgx
deb http://repo.download.nvidia.com/baseos/ubuntu/noble/x86_64/ noble-updates common dgx

clean http://archive.ubuntu.com/ubuntu
clean https://download.docker.com
clean https://nvidia.github.io
clean https://developer.download.nvidia.com
clean http://repo.download.nvidia.com
clean http://ppa.launchpad.net
```

Then create the target directory and run `apt-mirror`:

```bash
sudo mkdir /var/repos
sudo apt-mirror
```

## Transferring repositories to offline network

After downloading the repository contents, you will need to transfer the downloaded files to your offline network.

There are many ways to do this, depending on your local setup!
You should use the mechanism that gives you the best performance and ease-of-use in your environment.

One common way to accomplish this transfer is to bundle the downloaded files into an ISO file, which can then be moved to the offline environment or to a DVD or external USB drive.

```bash
sudo apt install genisoimage
sudo genisoimage -R -J -o /tmp/packages.iso /var/repos
```

(`-R -J` keep the long file names; a plain ISO 9660 image truncates them.)

## Create mirrors on offline network

Once the repository contents have been transferred to the offline network, they need to be made available as repositories for package installs.
Your offline environment may already have a package server, and there are many free and commercial solutions to do this!

If you don't already have a package server, the following process shows a minimal approach using an Apache httpd server.

First, in the offline network, pick a machine to use as your package server.
We will assume the use of the Apache httpd server and that the web root is `/var/www/html`:

```bash
sudo apt update
sudo apt install apache2
sudo mkdir /var/www/html/repos
```

Then, from the extracted mirror directory,
copy the directories for each repository into the web root.
For example, assuming the extracted mirror directory is `/var/repos` and the repository is `libnvidia-container`:

```bash
sudo cp -r /var/repos/mirror/nvidia.github.io/libnvidia-container/ /var/www/html/repos/libnvidia-container/
```

At this point, the downloaded package repositories should be available on your offline network via the package server.
Point DeepOps at them in your configuration; do not add sources by hand. DeepOps writes
each repository it uses itself, signed by its key (`roles/package_repos`, the Docker and
Container Toolkit roles), refuses entries in `/etc/apt/sources.list` (the Ubuntu archive
goes through `package_repos_ubuntu_mirror`), and a second, hand-written source for the
same repository with other `trusted`/`signed-by` options breaks apt:

```yaml
package_repos_ubuntu_mirror: http://repo-server/ubuntu                # the Ubuntu archive
docker_ubuntu_repo_base_url: http://repo-server/docker/linux/ubuntu  # its gpg key at <base>/gpg
nvidia_container_toolkit_repo_base_url: http://repo-server/libnvidia-container
package_repos_cuda_base: http://repo-server/cuda/repos               # <base>/ubuntu2404/x86_64/
```

The mirrors keep the upstream layout under each base (the roles append the same paths
they append upstream). The DGX repository is the one DeepOps does not manage; add it on
DGX systems as NVIDIA's DGX documentation describes:

```
deb http://repo-server/baseos/ubuntu/noble/x86_64/ noble common dgx
deb http://repo-server/baseos/ubuntu/noble/x86_64/ noble-updates common dgx
```
