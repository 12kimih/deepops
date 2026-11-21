# Slurm packages, upgrades and renaming

How DeepOps installs Slurm, the one-time settings it needs, how to change the Slurm
version of a running cluster (including the move from the older source build), and
how to rename a cluster.

- [Slurm packages, upgrades and renaming](#slurm-packages-upgrades-and-renaming)
  - [How Slurm is installed](#how-slurm-is-installed)
  - [One-time settings](#one-time-settings)
    - [The MUNGE key](#the-munge-key)
    - [The repository signing key](#the-repository-signing-key)
    - [Your own slurm.conf template](#your-own-slurmconf-template)
  - [Upgrading Slurm](#upgrading-slurm)
    - [What the playbook does](#what-the-playbook-does)
    - [Going back](#going-back)
  - [Moving from the source build](#moving-from-the-source-build)
  - [Renaming the cluster](#renaming-the-cluster)

## How Slurm is installed

`roles/slurm` installs SchedMD's Debian packages (`slurm-smd*`). They are built once
per version on `slurm-master[0]` from the release tarball, checked against
`slurm_checksums`, with the `debian/` directory the release ships
([Slurm's procedure](https://slurm.schedmd.com/quickstart_admin.html#debuild)), in a
throwaway Docker container of the host's own release (`slurm_deb_build_image`), never on
the host: nothing the host has installed (an older Slurm under `/usr/local`, other
libraries) can reach the packages. Root in the container installs the build
dependencies with `mk-build-deps`; the unprivileged account `slurmbuild` runs
`dpkg-buildpackage` on the tree, which stays on the host. The build uses the distribution's PMIx, hwloc,
Lua 5.4, curl, InfiniBand (ibmad, ibumad), MUNGE, MariaDB and PAM libraries and
NVIDIA's `cuda-nvml-dev-<slurm_nvml_cuda_version>` (the NVML header and link stub;
never `/usr/local/cuda`), and stops if a plugin in `slurm_deb_required_plugins` is
missing or `job_submit_lua.so` does not link Lua 5.4. dpkg never starts or restarts a
Slurm daemon (the build passes `--no-start`): the role restarts them in Slurm's order.
SchedMD's `debian/` directory packages none of the contribs its RPMs ship
(`slurm-contribs`: `seff`, `smail`, `sjobexitmod`, `sjstat`, and `sgather`); the build
adds them as `slurm-smd-contribs`, installed on every host.

The packages are versioned `<slurm_version>-1+deepops<slurm_deb_packaging>`. The
repository holds one build per version, so a change to what the build puts in the
packages raises `slurm_deb_packaging`: the next run builds the new version and moves
every host to it, a change within the release that needs no upgrade playbook.

pyxis (`slurm_pyxis_version`) is built in the same container from NVIDIA's own
`debian/` directory, against the `slurm-smd-dev` of this version, as
`nvslurm-plugin-pyxis` version `<pyxis>-1+slurm<slurm_deb_version>`: it depends on
exactly that `slurm-smd` (instead of enroot, which `roles/enroot` installs from
NVIDIA's releases) and is installed in the same apt transaction as Slurm wherever
`slurm_install_enroot` and `slurm_install_pyxis` are on. A SPANK plugin loads only in
the Slurm it was built for, so the two always change together; nothing is compiled
on the nodes. The same run rewrites `plugstack.conf.d/pyxis.conf` to the plugin's
path (`slurm_pyxis_*` options), or removes the entry and the package where pyxis is
switched off: a `required` SPANK plugin that cannot load stops `slurmd`.

The packages go into a flat apt repository in `slurm_repo_dir` (default
`/sw/.slurm-repo`, on the shared software filesystem), signed with
the key in `slurm_repo_signing_key_file`. Every node trusts that key for that repository only
(`signed-by`), installs exactly `slurm_deb_version`, and an apt pin keeps any upgrade
(unattended, or `playbooks/utilities/apt-upgrade.yml`) from moving it. A signed
repository rather than a trusted unsigned one: apt runs as root on every node, so
whoever could write the share (or alter NFS traffic, which AUTH_SYS does not protect)
would otherwise be root everywhere; with the signature, the share only stores files.
`slurm-master[0]` writes the repository as root, so it must be the NFS server of that
share or hold it on a local disk it exports.

The nodes keep no copy of the configuration (configless): see the
[deployment guide](README.md#how-slurm-is-installed).

## One-time settings

In the site configuration.

### The MUNGE key

The key every Slurm host shares is a vault-encrypted file in the site configuration,
`slurm_munge_key_file`, copied verbatim; nothing derives it from a password. When it
is missing, the first play adopts the key the hosts already share, or creates one on a
new cluster ([munge-key.md](munge-key.md)). Older
DeepOps derived the key from a password and `slurm_cluster_name`, so a rename changed
the key on every host at once: deploy the key file before anything else below, and
before any rename.

### The repository signing key

Two files in the site configuration: `slurm_repo_signing_key_file` (default
`config/files/slurm/repo-signing.key`, the ASCII-armored secret key, vault-encrypted)
and `slurm_repo_signing_pubkey_file` (`repo-signing.asc` beside it, the public key,
plain). The first play (`playbooks/slurm-cluster/site-secrets.yml`, `roles/site_secrets`)
creates the pair when both are missing: an ed25519 signing key without a passphrase and
without expiry, made in a throwaway GnuPG home on the controller. It never replaces
either file, and stops if only one of them exists. Commit both.

The build checks its signature against `slurm_repo_signing_pubkey_file` before
publishing, so a mismatched pair fails there and not on every node. Replacing the key
is a deliberate step: move both files away (or put your own pair there), run
`playbooks/slurm-cluster/slurm.yml` (the repository is signed again and every node gets
the new public key).

### Your own slurm.conf template

The controller refuses a `slurm_conf_template` that lacks `enable_configless` in
`SlurmctldParameters`, names the cluster other than `{{ slurm_cluster_name }}`, or sets
a `PluginDir` other than the packages' (leave it out; the same goes for a
`slurm_dbd_conf_template`). Add
`MetricsType=metrics/openmetrics` for the Prometheus scrape. With
`AutoDetect=nvml`, GPU types in `Gres=` must be substrings of the names NVML reports:
see [nvml.md](nvml.md#gpu-type-names).

## Upgrading Slurm

Set `slurm_version` (and its `slurm_checksums` entry, the SHA256 of the release
tarball), and regenerate Slurm's names for states and pending reasons, which the
cluster tools classify (`tests/cluster-tools/slurm-names.json`; the tests refuse a
file from another release, a vocabulary word the new release no longer sends and a
reason it adds that nothing classifies):

```bash
python3 scripts/deepops/slurm_names.py
python3 -m unittest discover -s tests/cluster-tools-lib
```

Then:

```bash
ansible-playbook -K playbooks/slurm-cluster/slurm-upgrade.yml
#   -e maintenance_serial=2            compute nodes per batch (default 1)
#   -e maintenance_drain_timeout=N     seconds to wait for a node's jobs (default 86400)
```

A plain deploy (`slurm.yml`, `slurm-cluster.yml`) refuses to change an installed
version.

### What the playbook does

Following [Slurm's upgrade guide](https://slurm.schedmd.com/upgrades.html):

1. Every host: refuses a downgrade, and a jump of more than `slurm_upgrade_max_majors`
   major releases (two; Slurm itself allows three since 24.11).
2. `slurm-master[0]`: builds and publishes the packages.
3. Controllers: a backup now (`slurm-backup@backup.service`: the accounting database,
   StateSaveLocation and the MUNGE key; `slurm_backup_destinations` must be set), stops
   `slurmctld` and `slurmdbd`, installs, starts `slurmdbd` (the first start of a new
   major release converts the database first; the role waits up to
   `slurm_dbd_wait_timeout`) and then `slurmctld`; checks `scontrol version` and
   `ping`, `sinfo`, `sacct`, the accounting cluster and, with the Lua plugin, a
   test-only submission through `job_submit.lua`. Running jobs keep running. A
   test-only submission only weighs nodes in service, so while every node is drained
   (a maintenance window) Slurm refuses it with "Requested node configuration is not
   available"; the check accepts that refusal then, and reports the node states.
4. Compute nodes, `maintenance_serial` at a time (roles/maintenance): drain and wait
   for the node's jobs, install (pyxis with it) and restart `slurmd`, check
   `slurmd`, the GPUs, DCGM and NHC, resume the node. A failing node stops the run
   and stays drained.
5. Login and other hosts: install (pyxis with it), restart `sackd`.
6. Every `slurmd` must report the new version.

### Going back

Slurm cannot go back once a new daemon has started: restore the backup taken in step 3
(the database dump, StateSaveLocation), with the old packages, still in the
repository, reinstalled by hand. Anything done after the backup is lost.

## Moving from the source build

Older DeepOps built Slurm with `make install` under `/usr/local`, with its own PMIx and
hwloc under `/opt/deepops`. The upgrade playbook replaces it, in the same order: on
each host it stops the daemons, runs `make uninstall` where the build tree is still
there, removes what the packages now provide from `/usr/local` (commands, libraries,
manual pages, `lib/slurm`, `include/slurm`), the PMIx and hwloc builds, their build
trees, `/etc/ld.so.conf.d/slurm.conf` and the unit copies in `/etc/systemd/system`,
then installs the packages. Compute nodes are drained first: their running jobs
would lose the old plugin directory.

Before it, in this order:

1. The MUNGE key file holding the current key ([munge-key.md](munge-key.md)) and
   deployed on its own, which the version check does not block:
   `ansible-playbook playbooks/slurm-cluster/slurm.yml --tags munge --check` must report
   no change to `/etc/munge/munge.key` on any host; then the same without `--check`.
2. The repository signing key pair set.
3. The settings the source build used, which the role now refuses, removed (the error
   names each): `slurm_autodetect_nvml`, `slurm_conf_symlink`, `pmix_install_prefix`,
   `hwloc_install_prefix` (an `openmpi_configure` naming them takes
   `--with-pmix=external --with-hwloc=external`), and the other retired ones.
4. A site `slurm.conf` template adapted (above).

## Renaming the cluster

Slurm has no rename. `slurmctld` refuses to start on state saved under another name
(`StateSaveLocation/clustername`), and accounting keeps each cluster's associations and
history under its name; the role refuses a changed `slurm_cluster_name` for that
reason. With the new name set:

```bash
ansible-playbook -K playbooks/slurm-cluster/slurm-cluster-rename.yml
```

It refuses unless every host already holds `slurm_munge_key_file`'s key. It drains every
partition, so nothing new is submitted while the name changes, then refuses unless no job
is left unfinished (let the jobs end, or cancel them, and run it again); a refusal puts
each partition back as it was. Then it takes a backup, adds the new cluster to
accounting, stops `slurmctld`, sets the old `clustername` file aside (kept as
`clustername.<old name>.<time>`), deploys the controller under the new name (the
accounting associations from `slurm_accounting_file` are created for it), and restarts
`slurmd` and `sackd`, which fetch the new configuration. The restarted `slurmctld` takes
each partition's state from `slurm.conf` again (it starts without `-R`), so the drain
ends with the rename. The old cluster's history stays in the database:
`sacct -M <old name>`, `sreport -M <old name>` (`gpu-usage JOB` looks in every cluster).

Everything else that shows the name follows `slurm_cluster_name`; a portal (Open
OnDemand) reads it from its cluster file, which its role writes from the same setting.
