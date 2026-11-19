# Software Modules

- [Software Modules](#software-modules)
  - [Introduction](#introduction)
  - [Installing Spack](#installing-spack)
    - [Configuration](#configuration)
    - [Usage](#usage)
    - [Architecture-specific builds](#architecture-specific-builds)
  - [Installing EasyBuild](#installing-easybuild)
    - [Configuration](#configuration-1)
    - [Usage](#usage-1)

## Introduction

Slurm clusters will frequently provide a bare-metal development environment in the form of "Environment Modules".
Environment Modules provide a convenient way to dynamically change the user's environment variables, such as `PATH` or `LD_LIBRARY_PATH`, to enable use of software installed in different locations.
This makes it easy to install multiple versions of the same software package and dynamically switch between them, such as different versions of CUDA or OpenMPI.

DeepOps installs the Lmod tool for managing your Environment Modules.
The [Lmod documentation](https://lmod.readthedocs.io/en/latest/) provides more information on using Lmod and writing Modules.

DeepOps also provides tooling for managing Environment Modules with two general build
frameworks -- EasyBuild and Spack -- plus a dedicated `nvidia_cuda_toolkit` role for CUDA.
The two build frameworks provide a selection of tested build recipes for common
development tools and scientific software packages, making it easier to install software
without a tedious manual build process.

For **CUDA specifically**, the `nvidia_cuda_toolkit` role
(`playbooks/slurm-cluster/nvidia-cuda-toolkit.yml`) installs the newest patch of every
CUDA minor from `cuda_toolkit_oldest` (default 11.8) up to the newest the driver branch
runs, from the official NVIDIA runfiles into a shared `/sw/cuda/<version>` tree, with one
Lmod modulefile per minor: users `module load cuda/12.4` on any node (`cuda/12.4.1` loads
the same toolkit, and a bare `module load cuda` the newest; the host driver is never
touched). A patch release replaces the previous patch of its minor without changing the
module name a job script loads. A toolkit exists only in its tree: the role installs one at a
time after checking free space (`cuda_install_free_gb`), deletes each runfile once installed,
and removes what the runfile writes host-wide (`/usr/local/cuda`, `/etc/ld.so.conf.d/cuda-*.conf`,
pkg-config and nsight desktop files pointing into the tree), so nothing outside a loaded module
resolves CUDA. This is the per-version, modules-based
alternative to the system-wide `nvidia_cuda` install -- see
[Choosing between similar roles and playbooks](../deepops/choosing-roles-and-playbooks.md).

## Installing Spack

[Spack](https://spack.io/) is a package manager for supercomputers, Linux, and MacOS which makes installing scientific software easy.

To install Spack using DeepOps, you can run:

```bash
ansible-playbook -l slurm-cluster playbooks/slurm-cluster/spack-modules.yml
```

### Configuration

Spack is cloned at the release tag `spack_version`, and its builtin package repository
(spack/spack-packages, separate since Spack 1.0) once into `spack_packages_dir` at
`spack_packages_version`, so no user's Spack clones it into their home. Both live under
the shared prefix; the settings sit in the instance's site scope
(`$SPACK_ROOT/etc/spack/site`).

- `spack_install_dir`: where Spack is installed, on a filesystem every node shares.
  The default is `/sw/spack`.
- `spack_default_packages`: specs the role installs into the shared instance. None by default.
- `spack_lmod_core_compilers`: compilers whose packages go in the Core layer of the Lmod
  hierarchy. The default is `[gcc]`.

### Usage

Spack generates an Lmod module for each package it installs, in a Core/compiler/MPI
hierarchy under `$SPACK_ROOT/share/spack/lmod`. The playbook registers the Core layer
with Lmod on every node, so `module avail` lists them and `module load` works anywhere:

```bash
login01:~$ module avail
---------------------------- /sw/spack/share/spack/lmod/Core ----------------------------
   cuda/13.0.2    gcc/14.3.0    openmpi/5.0.8 ...
```
Use "module keyword key1 key2 ..." to search for all possible modules matching any of the "keys".
```

You can then load a chosen module using `module load`.
Loading a module will change your active environment variables (such as `PATH`) to add the software package in question to your environment.

```bash
login01:~$ module load cuda-10.2.89-gcc-7.5.0-sozilk3
login01:~$ which nvcc
/sw/spack/opt/spack/linux-ubuntu18.04-ivybridge/gcc-7.5.0/cuda-10.2.89-sozilk3ahqmsg3nndyifhv7hhw2j6cgt/bin/nvcc
```

In addition to using Environment Modules directly, Spack also provides a mechanism to load and unload modules directly using the `spack` command.

```bash
login01:~$ spack find
-- linux-ubuntu24.04-x86_64_v3 / %c,cxx=gcc@13.3.0 --------------------
cuda@13.0.2  openmpi@5.0.8 ...
login01:~$ spack load cuda
login01:~$ which nvcc
/sw/spack/opt/spack/linux-x86_64_v3/cuda-13.0.2-.../bin/nvcc
```

For more information on using Spack, see the [Spack documentation](https://spack.readthedocs.io/en/latest/).

### Architecture-specific builds

It's important to note that, by default, Spack will detect and optimize for the specific microarchitecture where packages are being built.
This will likely improve performance for many packages!
However, if you run multiple CPU microarchitectures on your cluster, you may wish to build the same packages multiple times, once on each architecture.

If you prefer to avoid optimizing for a specific microarchitecture, you can target the generic `x86_64` architecture by adding `target=x86_64` to the spec.
This will produce less-optimized but more generic builds.

## Installing EasyBuild

[EasyBuild](https://docs.easybuild.io/) is a software build and installation framework that allows you to manage (scientific) software on HPC systems in an efficient way.

To install EasyBuild using DeepOps, you can run:

```bash
ansible-playbook playbooks/slurm-cluster/easybuild-modules.yml
```

Lmod goes on every node. EasyBuild and its builds go once on the host that owns the shared
tree, `slurm-master[0]` unless you pass `-e hostlist=<host>`. The `easy-build` role follows
the [official pip installation](https://docs.easybuild.io/installation/):

- **EasyBuild itself:** `pip install --require-hashes` into a root-owned venv per version,
  `/sw/software/EasyBuild/<version>`, from a requirements list pinned by sha256 (the
  `easybuild`, framework, easyblocks and easyconfigs sdists, and the setuptools that builds
  them, so pip fetches nothing unverified). The modulefile `EasyBuild/<version>` lands in
  the shared module tree, `/sw/modules/all`.
- **The build account:** EasyBuild refuses to run as root, so builds run as an
  unprivileged system account (`easybuild_user`, default `easybuild`). It owns only
  EasyBuild's own tree, `/sw/easybuild` (software, modules, sources), and its home
  `/var/lib/easybuild`, where builds unpack on local disk. The rest of `/sw` and the shared
  module tree stay root's.
- **Configuration:** `/etc/xdg/easybuild.d/deepops.cfg` (root, `0644`) sets the prefix, the
  module path, Lmod and Lua modulefiles. EasyBuild 5 reads `$XDG_CONFIG_DIRS/easybuild.d`,
  default `/etc/xdg`; `/etc/easybuild.d` (as the installation page still says) is only
  warned about and not read. The robot path is EasyBuild's default, the easyconfigs it
  ships.
- **Module trees:** the `lmod` role registers `lmod_module_paths` on every node, the shared
  tree first and then `/sw/easybuild/modules/all`, so what EasyBuild builds is found
  everywhere.

EasyBuild 5 needs Lmod 8.0 or newer and Python 3.9 or newer for the venv
(`easybuild_python`). The venv uses the build host's interpreter, so `module load
EasyBuild` works on nodes with the same Python at the same path.

### Configuration

- `easybuild_version`: The EasyBuild release (default `5.4.2`). A new version needs its
  sdist checksums in `easybuild_sha256` (see `roles/easy-build/defaults/main.yml`).
- `easybuild_user`: The build account (default `easybuild`).
- `easybuild_prefix`: EasyBuild's own tree, the only part of `/sw` the build account owns
  (default `/sw/easybuild`).
- `easybuild_packages`: Easyconfigs to build with `eb --robot` as the build account, each a
  file name from the easyconfigs EasyBuild ships (find them with `eb --search <name>`) or
  the path to a site recipe. Dependencies are built too; something already installed is
  not rebuilt, and the task reports changed only when something was built. Default `[]`.

To run EasyBuild by hand, become the build account with a login shell:

```bash
sudo -u easybuild bash -l
module load EasyBuild
eb --search zlib
eb --robot zlib-1.3.1-GCCcore-14.2.0.eb
```

### Usage

Once you have built software with EasyBuild, `module avail` lists it on every node:

```bash
login01:~$ module avail

------------------------------- /sw/modules/all --------------------------------
   EasyBuild/5.4.2    cuda/12.9

---------------------------- /sw/easybuild/modules/all ----------------------------
   GCCcore/14.2.0    binutils/2.42    zlib/1.3.1-GCCcore-14.2.0
```

You can then load a chosen module using `module load`.
Loading a module will change your active environment variables (such as `PATH`) to add the software package in question to your environment.

```bash
login01:~$ module load zlib/1.3.1-GCCcore-14.2.0
login01:~$ echo $EBROOTZLIB
/sw/easybuild/software/zlib/1.3.1-GCCcore-14.2.0
```

For more information on using EasyBuild, see the [EasyBuild documentation](https://docs.easybuild.io/).
