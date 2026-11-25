# The job environment: per-user directories around Slurm jobs

Containers in jobs (Enroot and Pyxis, rootless Podman) keep per-user state on each
node. This page lists every such directory, the one setting that places it, and what
creates and removes it. The prolog, task prolog and epilog parts live in
`roles/slurm/templates/etc/slurm/` (`prolog.d`, `task-prolog.d`, `epilog.d`), run in
name order by `shared/bin/run-parts.sh` (prolog, epilog) and `task-prolog.sh`.

| Directory | Set by (one place) | Created | Removed |
|---|---|---|---|
| Job runtime dir, the job's `XDG_RUNTIME_DIR` (`/run/slurm-users/<uid>`) | `slurm_user_runtime_root` | prolog `40-all-runtime-dir` (root, 0700, owned by the user) | epilog `50-lastuserjob-runtime-dir`, with the user's last job on the node |
| enroot runtime (`/run/enroot/user-<uid>`) | `enroot_runtime_path`, written to `/etc/enroot/enroot.conf.d/90-deepops.conf` | prolog `50-all-enroot-dirs` (as the user) | epilog `50-lastuserjob-all-enroot-dirs`, last job |
| enroot data, unpacked containers (`/tmp/enroot-data/user-<uid>`) | `enroot_data_path` (same file) | prolog `50-all-enroot-dirs` | named containers of the job: pyxis (`container_scope=job`); the rest: epilog `50-lastuserjob-all-enroot-dirs`, last job |
| enroot cache, image layers (`/var/lib/enroot-cache/user-<uid>`) | `enroot_cache_path` (same file) | prolog `50-all-enroot-dirs` | `container-storage-gc` daily: unused 14 days, user left, or disk full |
| pyxis runtime (`/run/pyxis/<uid>`) | `slurm_pyxis_runtime_path` (plugstack.conf) | pyxis | its squashfs files by pyxis; the directory is empty after each step |
| podman storage (`/var/lib/podman-storage/<uid>`) | `podman_storage_root`, written to `/etc/containers/storage.conf` | `roles/podman`, per roster person | `container-storage-gc` daily, as for the enroot cache |
| podman runtime (`$XDG_RUNTIME_DIR/containers`, `libpod`) | the job runtime dir | podman | with the job runtime dir |

The prolog, epilog and cleanup read the enroot and podman paths from the tools' own
files at run time (`shared/lib/enroot-paths.sh`, `cluster_tools.container_gc`), so a
path changed in one setting is followed everywhere.

## Task prolog

`TaskProlog=/etc/slurm/task-prolog.sh` runs as the job user before every task of every
step. Each executable part in `task-prolog.d` prints `export NAME=value` lines, which
Slurm applies to the task's environment
([prolog and epilog guide](https://slurm.schedmd.com/prolog_epilog.html)). Only
`export` and `unset` lines pass, a part gets 10 seconds, and a failing part is
skipped: a task prolog that fails cancels the task. Pyxis builds its container
environment from the job's submitted environment, not from these exports.

## Thread counts

Libraries that size their thread pool by the machine (OpenMP, MKL, OpenBLAS, Python's
`os.cpu_count()` from 3.13, and GNU `nproc`, which reads `OMP_NUM_THREADS`) would start
one thread per CPU of the node: far more than a login's CPU quota, or a job worker's
share, and throttled for it. `user_environment_thread_variables`
(`roles/user_environment_defaults`) are set from `cluster_tools.cpus`, the one count the
repository's own tools use too:

- in a login (`/etc/profile.d`, `roles/cluster_tools`), to the CPUs the login may use: its
  cpuset, capped by every cgroup CPU quota above it (a login node's per-person slice);
- in a task (`task-prolog.d/65-thread-counts`), to each worker's share: `--cpus-per-task`
  when given, else the job's CPUs on the node over its tasks there or its GPUs there,
  whichever is more (torchrun starts one worker per GPU).

`CLUSTER_CPUS` carries the number set, so the task replaces a value its login set and
keeps one the user chose: a person's own value wins in both. `[]` sets none.

## Per tool

- **Enroot** (`roles/enroot`, `enroot_version`, standard build): configured in
  `enroot.conf.d/90-deepops.conf` as the
  [pyxis setup guide](https://github.com/NVIDIA/pyxis/wiki/Setup) recommends, except
  `ENROOT_MOUNT_HOME y` (the NFS homes are expected inside containers). The layer
  cache had no cleanup before `container-storage-gc`.
- **Pyxis** (packaged and configured by `roles/slurm`, `slurm_pyxis_version`): `container_scope=job` removes a job's named
  containers in pyxis' own job epilog, which runs enroot as the job user, so the
  `$(id -u)` paths expand correctly. `use_enroot_load=1` takes only registry images
  and squashfs files: a `podman://` image is imported to a squashfs file first
  (`enroot import`, see [rootless Podman](../container/podman.md)). The PMIx settings
  for slurmd (`roles/slurm/tasks/compute.yml`) are the guide's, by owner decision.
- **NVIDIA Container Toolkit** (`roles/nvidia_container_toolkit`, `nvidia_container_toolkit_version`): enroot's
  `98-nvidia.sh` hook uses `nvidia-container-cli`; podman uses the CDI specification
  the package's `nvidia-cdi-refresh` units keep, which the role keeps enabled and
  `nvidia-mig.yml` refreshes after a MIG change.
- **Podman** (`roles/podman`): see [rootless Podman](../container/podman.md).
