# Software Modules

- [Software Modules](#software-modules)
  - [Introduction](#introduction)
  - [Installing EasyBuild](#installing-easybuild)
    - [Configuration](#configuration)
    - [Usage](#usage)

## Introduction

Slurm clusters will frequently provide a bare-metal development environment in the form of "Environment Modules".
Environment Modules provide a convenient way to dynamically change the user's environment variables, such as `PATH` or `LD_LIBRARY_PATH`, to enable use of software installed in different locations.
This makes it easy to install multiple versions of the same software package and dynamically switch between them, such as different versions of CUDA or OpenMPI.

DeepOps installs the Lmod tool for managing your Environment Modules.
The [Lmod documentation](https://lmod.readthedocs.io/en/latest/) provides more information on using Lmod and writing Modules.

### `module` in every shell

Users never edit their startup files: `module` works in bash, zsh, tcsh and fish, in a
login, an interactive shell without a login (`srun --pty bash`), a command over ssh
(`ssh node module list`), a non-interactive zsh, and a bash script started from any of
them, as the [Lmod docs](https://lmod.readthedocs.io/en/latest/030_installing.html) ask.

- `roles/login_shells` installs every shell `people.yml` gives an active person, and bash (`login_shells`), on every host (an account
  whose shell a node lacks cannot log in there) and makes each login read
  `/etc/profile.d`, the one place roles put the login environment. Debian's zsh and tcsh
  read none of it on their own.
- `roles/lmod` links Lmod's init as `/etc/profile.d/z00_lmod.{sh,csh}` and
  `/etc/fish/conf.d/z00_lmod.fish`, and puts a hook first in each installed shell's
  system rc file (`/etc/bash.bashrc`, `/etc/zsh/zshenv`, `/etc/csh.cshrc`), before any
  early return for shells that are not interactive.
- `roles/cluster_tools` writes the users' environment variables, one list
  (`user_environment` over the defaults in `roles/user_environment_defaults`: HF_HOME,
  the uv and pip caches, SHARED_HF_HUB_CACHE where the site has a model cache), as
  `/etc/profile.d/z10-cluster-tools.{sh,csh}`; the Slurm task prolog gives a job the
  value that differs there (the package caches in the job's own `/tmp`). Each is a
  default: a value the user set wins.
- Each deploy proves it: `roles/lmod/files/lmod-shell-check.py` runs `module --version`
  as `nobody` with a clean environment in every way each installed shell starts.

A script must start with `#!/bin/bash` (bash reads `BASH_ENV`; `#!/bin/sh` does not).
A cron job has no session: begin it with `. /etc/profile.d/z00_lmod.sh`.

DeepOps also provides tooling for managing Environment Modules with a general build
framework, EasyBuild, plus a dedicated `nvidia_cuda_toolkit` role for CUDA. EasyBuild
provides a selection of tested build recipes for common development tools and scientific
software packages, making it easier to install software without a tedious manual build
process. (The Spack role was retired in 2026-11: build with EasyBuild, or in a container.)

For **CUDA specifically**, the `nvidia_cuda_toolkit` role
(`slurm_install_cuda_toolkit: true`; `slurm-cluster.yml` or
`playbooks/slurm-cluster/nvidia-cuda-toolkit.yml` deploys it on the first slurm-master,
which owns the shared tree, and turning it off removes the toolkits) installs the newest patch of every
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

## Installing EasyBuild

[EasyBuild](https://docs.easybuild.io/) is a software build and installation framework that allows you to manage (scientific) software on HPC systems in an efficient way.

To install EasyBuild using DeepOps, set `slurm_install_easybuild: true`; `slurm-cluster.yml`
deploys it, or on its own:

```bash
ansible-playbook -K playbooks/slurm-cluster/easybuild-modules.yml
```

EasyBuild and its builds go once on the host that owns the shared tree, the first
slurm-master; turning the switch off removes EasyBuild's venvs, modulefiles and
configuration from there, never what it built (`easybuild_prefix`). The `easy-build` role follows
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

- `easybuild_version`: The EasyBuild release (the current default is in the [version table](../deepops/versions.md)). A new version needs its
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
