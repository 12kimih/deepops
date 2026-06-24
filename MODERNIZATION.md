# DeepOps Modernization -- Change Log (branch `main`, formerly `modernize`)

This document explains **every change made on the `main` branch (formerly `modernize`) since the
forked NVIDIA DeepOps baseline** (commit `8858399d`, "release 26.05"): *what* was
changed, *how*, and *why*. It is meant for review before merging.

- **Goal:** keep this fork as a clean, public, continuously-maintained DeepOps --
  latest pinned versions, official-docs-compliant (with citations in code),
  consistent/linted, idempotent + boot-persistent.
- **Target OS matrix:** Ubuntu 22.04 / 24.04 / 26.04 + RHEL-family 8 / 9 / 10
  (RHEL / Rocky / Alma).
- **Verification:** every change validated with `yamllint` (0 errors) and
  `ansible-lint roles playbooks` (**production** profile; clean except the two
  `kubespray_defaults` syntax-checks, which only resolve when the kubespray
  submodule is checked out). The tree was additionally reviewed file-by-file by
  multi-agent workflows -- against the baseline, the OS matrix, per-program official
  docs, and for repo-wide consistency -- fixing findings until each converged.
- **Diff vs baseline:** the bulk of the line count is mechanical FQCN / naming /
  formatting normalization (section 1, section 12); the substantive changes are in section 2-section 16.

---

## 0. Architecture decisions (agreed up front)

| Topic | Decision | Why |
|---|---|---|
| Real cluster config + secrets | Keep them in the gitignored `config/` overlay, versioned in a **separate private repo**; encrypt passwords with **Ansible Vault**. Files DeepOps does **not** consume (netplan, IPMI/BMC) live in a separate private infra repo, never in DeepOps. | Keeps the public repo free of cluster topology/secrets while staying version-controlled. DeepOps already supports the `config/` overlay pattern. |
| Modified Galaxy roles | **Vendor in-tree** the roles we diverge on, and drop their Galaxy pins. | We heavily modified driver/enroot and the old Galaxy versions were EOL; in-tree roles are tracked, reviewable, and can't be clobbered by `ansible-galaxy install`. |
| Linting | Raise to ansible-lint **production** profile + a documented deferred skip-list; enforce in CI. | Locks in consistency so it can't regress. |

---

## 1. Lint / formatting baseline + repo-wide consistency  (`30c99d9`)

**What:** Added `.yamllint`, `.pre-commit-config.yaml`, a `yamllint` GitHub
Actions workflow; raised `.ansible-lint` to `profile: production`; normalized the
whole tree.

**How:**
- `.yamllint`: objective hygiene rules as errors (truthy, trailing whitespace,
  end-of-file, octal modes, document-start, LF endings); subjective rules
  (line-length, indentation, comments) as warnings. `templates/` excluded
  (Jinja/Helm aren't valid standalone YAML).
- Repo-wide normalization: **FQCN** for all module calls; **capitalized** task/
  play/handler names; `yes/no` -> `true/false` (363); quoted octal modes (62);
  jinja spacing; task key ordering; trailing-whitespace / document-start / CRLF.
- Fixed a duplicate key in `config.example/nvidia-mig-config.yml`.
- Documented the Ansible Vault + private-config-repo workflow in
  `docs/deepops/configuration.md`.

**Why:** The baseline mixed `yes/no` and `true/false`, missing FQCNs,
inconsistent task naming/quoting, trailing whitespace, etc. Consistency was the
top priority. A few **corruption issues introduced by the automated fixer** were
caught and repaired (see section 8): a Jinja filter mis-capitalized (`default`->`Default`)
and handler renames whose `notify:` callers weren't updated (case-sensitive).

---

## 2. NVIDIA driver (in-tree) + optional CUDA  (`725239c3`)

**What:** Replaced the `nvidia.nvidia_driver` Galaxy role with an in-tree
`roles/nvidia_driver`; made the system-wide CUDA toolkit optional.

**How:**
- New `roles/nvidia_driver` installs the driver from the **single official
  meta-package per OS**: Ubuntu `nvidia-driver-{branch}-server-open`; RHEL/Rocky
  `dnf module enable nvidia-driver:{branch}-open` + `nvidia-open`. Fully
  configurable: `nvidia_driver_branch`, `nvidia_driver_kernel_modules`
  (open/proprietary), `nvidia_driver_server_branch`, or full override via
  `nvidia_driver_package` / `nvidia_driver_rhel_*`. Optional purge-before-install,
  reboot-if-changed, `nvidia-smi` verify. Multi-OS, doc URLs cited inline.
- CUDA: `nvidia_cuda_install` (default false -- system CUDA is served as Lmod
  modules instead, see section 20) gates the toolkit; the slurm flow keeps
  `slurm_cluster_install_cuda`.

**Why:** The old path listed **both** the dkms and no-dkms `-open` packages and
the Galaxy role installed each package in a separate `apt` loop -> no coherent
kernel module built -> `nvidia-smi` failed. The old open-module path was also
gated off by default, and RHEL had **no** open-module support at all. The
single-meta approach matches the manual `apt install nvidia-driver-580-server-open`
that is known to work. *(Note: a follow-up docs audit of post-install actions --
`nvidia-persistenced` etc. -- is in progress; see section 9.)*

---

## 3. Container / runtime stack

### 3a. NVIDIA Container Toolkit + docker boot-enable  (`76f7a09a`)
- **Pin** the unified toolkit package set (`nvidia-container-toolkit` + base +
  `libnvidia-container-tools` + `libnvidia-container1`) to `1.19.1-1`
  (`nvidia_container_toolkit_version`, empty = latest).
- **Enable docker at boot** (`nvidia_container_toolkit_enable_docker`): on RHEL
  `docker-ce` does not auto-enable, so docker-based services (exporters, registry)
  were dead after reboot -- this closes the top boot-persistence gap.
- Route **all** supported OSes (Ubuntu 22.04+/EL 8+) through the native toolkit
  role and **remove the EOL `nvidia.nvidia_docker`** (nvidia-docker2) fallback.
- Fixed the `docker_install | default('yes')` truthy bug (always truthy ->
  `default(true) | bool`).
- **Why:** `nvidia-docker2`/`nvidia-container-runtime` are end-of-life; the
  unified toolkit + `nvidia-ctk runtime configure` is the current path.

### 3b. enroot 4.2.0 in-tree  (`47213d15`)
- New `roles/enroot` installs **enroot 4.2.0** from the release `.deb`/`.rpm`
  (version-pinned, with a remove/reinstall step for clean upgrades).
- Sets `user.max_user_namespaces` / `user.max_mnt_namespaces` (hard prerequisite).
- **Ubuntu 23.10+/24.04 AppArmor:** relies on enroot's **bundled scoped AppArmor
  profile** (loaded via handler) instead of globally disabling
  `kernel.apparmor_restrict_unprivileged_userns` -- a much smaller security blast
  radius. A global-disable escape hatch is available for trusted nodes.
- RHEL: enables unprivileged userns on the kernel cmdline (grubby) if missing.
- Per-user runtime/cache/data dirs are created `0700` by the existing Slurm
  prolog (not world-writable tmpfiles).
- **Why:** the old `nvidia.enroot` v0.5.0 was an EOL 3.x release with no handling
  for the unprivileged-userns restriction modern Ubuntu enables by default --
  this is the "enroot fails after install on Ubuntu 24.04" problem. Verified
  against the official enroot docs: `+caps` does **not** remove the userns
  requirement (caps apply only to image-import helpers), so the scoped AppArmor
  profile is the correct, standard fix.

### 3c. pyxis 0.24.0  (`32c040f9`)
- Bump pyxis 0.11.1 -> **0.24.0**, rebuilt against the deployed Slurm headers
  (pyxis is ABI-bound to Slurm -- must be rebuilt on a Slurm bump).
- Add `make install` (plugin at `{{ slurm_install_prefix }}/lib/slurm/spank_pyxis.so`).
- Parameterize `plugstack.conf.d/pyxis.conf` (runtime_path, execute_entrypoint,
  container_scope, sbatch_support, `use_enroot_load=1`).

---

## 4. SLURM 25.11.x compliance  (`5ddd33a3`, `4922b2cb`)

- **build deps:** add the cgroup/v2 (`dbus`, `bpf`) and REST-API (`json-c`,
  `http-parser`, `yaml`) libraries plus NUMA, Lua, readline for Ubuntu and EL.
- **slurm.conf / cgroup.conf / slurmdbd.conf / gres.conf:** the role's
  `etc/slurm/*` templates are kept at the upstream DeepOps baseline (already free
  of the removed `CryptoType`/`FastSchedule`/`cons_res`; `auth/munge` +
  `accounting_storage/mysql`; `AutoDetect=nvml`). Full site control of the four
  files is via complete templates under `config/files/slurm/` pointed to by the
  `slurm_*_conf_template` vars.
- **Why:** cgroup/v1 is deprecated in 25.11 and `dbus`/`bpf` are build-time
  requirements for the v2 plugin; the REST/Lua libs were missing.
- **Refs:** `https://slurm.schedmd.com/archive/slurm-25.11.6/{cgroup.conf,quickstart_admin,slurm.conf,slurmdbd.conf,gres.conf}.html`

---

## 5. Monitoring stack  (`dfab757d`)

- **Uniform systemd hardening** on all six docker-based units (prometheus,
  grafana, alertmanager, node-exporter, slurm-exporter, dcgm-exporter): order
  after `network-online.target` (so the image pull doesn't race the network on
  boot) and add `RestartSec=10` (avoid crash-loop hammering).
- **Version bumps:** prometheus `v3.11.3`->`v3.12.0`, alertmanager `v0.32.1`->
  `v0.32.2`, grafana `13.0.1`->`13.0.2`. node-exporter `v1.11.1` and dcgm
  `4.5.3-4.8.2-distroless` already current.
- **slurm-exporter:** set `slurm_exporter_container` to your own built image
  (pin a versioned tag in production).

---

## 6. OS matrix support -- Ubuntu 22.04/24.04/26.04 + EL 8/9/10  (`2acc8e8e`, `0ab38774`, `6f2955c0`, `3169f2f9`)

A file-by-file review of 80 OS-conditional files found and fixed real breakage:

- **EL9/10 `crb`:** the CodeReady Builder repo was renamed from `powertools`
  (EL8) to `crb` (EL9+). Hardcoding `powertools` broke the Slurm build, Lmod, and
  Singularity deps on EL9/10. Now derived from the major version.
- **dns-config:** systemd-resolved was only disabled on Ubuntu 16/18/20 -> static
  DNS never applied on 22.04/24.04/26.04. Now disabled on 18.04+ and the resolv.conf
  stub symlink is replaced before templating.
- **nis_client:** the restart handler was gated to Ubuntu 14.04 and never fired
  on modern Ubuntu -> config changes weren't applied. Now a single `nis` handler.
- **docker-rootless:** persist `br_netfilter` via `/etc/modules-load.d` (read on
  both Debian and RHEL) instead of `/etc/modules` (Debian-only).
- **ood-wrapper:** install `python3-passlib` on Ubuntu + EL8/9/10 (was EL8-only
  and pulled the removed python2 package).
- **mofed:** tolerate EL9/10 (fall back to EL8 prereqs); flagged that
  `mofed_version` 5.6 (2022) needs a bump/DOCA-OFED for current OSes.
- **Ubuntu 26.04 forward-compat:** `nvidia_cuda` / `nvidia_dcgm` repo release is
  overridable so 26.04 can fall back to a published path until NVIDIA ships
  `ubuntu2604`; declared EL10 in role metadata.

**Principle applied throughout:** use `>=`-style version gates and codename
auto-detection so 26.04/EL10 and future releases are included automatically.

---

## 7. Playbook ports + global boot-persistence/idempotency  (`b96016d5`, `4fab4192`)

Ported from the operator's private playbooks, rewritten in DeepOps style
(`hostlist` default, FQCN, `proxy_env`):
- `playbooks/utilities/disable-acs.yml` -- persistent oneshot unit disabling PCIe
  ACS for NVIDIA NCCL GPU P2P (reapplied every boot).
- `playbooks/utilities/verify-acs.yml` -- reads back the ACS Control register on
  every PCI device and checks the disable-acs unit is enabled/active; fails if ACS
  is still on anywhere. Also a read-only IOMMU/VT-d audit (BIOS VT-d via the DMAR
  ACPI table, kernel intel_iommu state) for fleet-wide consistency checks without
  rebooting into firmware.
- `playbooks/nvidia-software/nvidia-vulkan.yml` -- Vulkan runtime/tools/Mesa.
- `playbooks/utilities/apt-upgrade.yml` -- hostlist-parameterized dist-upgrade.
- `playbooks/utilities/reboot.yml` -- rolling reboot (`reboot_serial`).

Boot-persistence / idempotency fixes:
- `nvidia-peer-memory`: enable the `nv_peer_mem` service (`enabled: true`) and
  persist the kernel module so it survives reboot.
- registry / nginx-cache: drop the non-idempotent `docker_container restart: true`
  (forced bounce every run); `restart_policy: unless-stopped` already gives boot
  persistence.
- `cachefilesd`: `cachefilesd_enabled` was the string `present`; made it a boolean.

---

## 8. Safety / verification notes

- The automated lint fixer (`ansible-lint --fix`) re-serializes whole files,
  which once folded long command lines, mis-capitalized a Jinja filter, and
  renamed handlers without updating `notify:` callers. **All were detected by a
  file-by-file diff-vs-baseline review and repaired** (60+ `notify` references
  reconciled, `Default`->`default` restored, no token corruption remained).
- A wrong FQCN guess (`ansible.mysql.mysql_user`) was corrected to
  `community.mysql.mysql_user` (canonical on the CI's ansible 10.7.0).
- **Recurrence prevention:** CI + pre-commit run yamllint/ansible-lint on every
  change; long commands should use block scalars; prefer targeted edits over
  whole-file `--fix`.

---

## 9. Known follow-ups (intentionally deferred)

- **NVIDIA driver post-install / install-method -- DONE** (commit `6d290421`):
  audited against the official driver guide. Added `nvidia-persistenced`
  enablement (the recommended daemon, over the deprecating `nvidia-smi -pm 1`);
  added `nvidia_driver_install_method` (default `ubuntu_repo` = Canonical signed
  `-server-open`; `nvidia_repo` = cuda-keyring + `nvidia-driver-pinning-<branch>`
  + `nvidia-open`); added opt-in `nvidia_driver_fabricmanager` for NVSwitch HGX;
  and `cat /proc/driver/nvidia/version` verification. Standard practice for a
  stock-kernel Slurm fleet is the Canonical `-server-open` default (Secure Boot
  works without MOK enrollment); the NVIDIA-repo path is the opt-in alternative.
- **nvidia-dgx 26.04/EL10 gates -- DONE** (section 11): the role no longer hard-fails on
  26.04/EL10 (lower-bound version gates), and the EL7/18.04/20.04 DGX-OS legacy
  was removed.
- **ood-wrapper EL8+ -- DONE** (section 11): `vars/redhat.yml` now uses base-OS `httpd` +
  `python3` instead of the EL7 SCL (`httpd24`, python2) packages.
- **mofed -- DONE** (section 16-b, `73c627e8`): migrated to DOCA-OFED (the MLNX_OFED
  successor) via NVIDIA's DOCA network repo + `apt/dnf install doca-ofed`,
  release-aware, with a **validation-required** note (DOCA pins driver/firmware per
  release, so the operator confirms the repo URL/version for their adapter+OS on the
  DOCA downloads page; untestable here without IB hardware). `roce_backend`'s bundled
  MLNX_OFED 4.7 / Ubuntu 18.04 ISO is flagged legacy and points at the `mofed` role.
- **ufw firewall** -- port as a parameterized firewall role (the source hardcoded
  subnets/ports).

---

> The sections below (10+) cover the **continued modernization** after the initial
> review: driver idempotency, legacy-OS removal, the repo-wide consistency pass,
> two web-research audits (persistenced / AppArmor), the Slurm 25.11.6 config
> overhaul + the reference-deployment port, and prerequisite-package fixes.

## 10. NVIDIA driver idempotency + post-install hardening  (`142480b6`, `b83476fa`)

- **Idempotent purge (critical fix).** The pre-existing-driver purge matched the
  driver the role had just installed, so every re-run purged it, reinstalled, and
  rebooted (a reboot loop). Now the target package is resolved first, package
  facts are gathered, and the find/purge/module-reset only run when the desired
  driver is **not** already installed (still fires on a real branch/flavor change).
- **Post-install guard.** A `nvidia_driver_module_loaded` fact gates the
  persistence-daemon enable and the `nvidia-smi` / `/proc/driver/nvidia/version`
  verification so they are skipped (not failed) when a fresh install ran with the
  reboot suppressed.
- **persistenced drop-in (opt-in).** Web research confirmed plain
  `systemctl enable --now nvidia-persistenced` is correct on current drivers
  (the daemon enables persistence mode by default; the packaged unit already runs
  as `--user nvidia-persistenced`), so the manual `--persistence-mode` drop-in is
  redundant. Exposed as opt-in `nvidia_driver_persistenced_dropin` (default false)
  for those who want `--verbose`.

## 11. Legacy-OS removal + supported-OS gate fixes  (`eb46e09b`)

A file-by-file OS-matrix/legacy audit (12 reviewers) drove these. Removed code that
served **only** Ubuntu <=20.04 / EL<=7, and fixed gates that rejected supported releases:

- **nvidia-dgx:** deleted `ubuntu-legacy.yml` + `vars/ubuntu-18.04.yml` +
  `vars/ubuntu-20.04.yml` (DGX OS 4/5), `redhat-legacy-el7.yml` + its include/guard,
  the bionic gpgkey/apt-repo defaults, and the orphaned `sources.list.j2`. The
  Ubuntu dispatcher now uses a `>= 22.04` lower-bound and routes 24.04/26.04 to the
  DGX OS 7 path (was a hardcoded `== 24.04` allowlist that failed on 26.04); the
  RHEL dispatcher supports majors 8/9/10 (`int >= 8`).
- **ood-wrapper:** dropped the EL7 `python-passlib` task and the EL7 `httpd24` SCL
  PATH override; `vars/redhat.yml` now targets base-OS `httpd` + `python3`.
- **mofed/nhc:** deleted dead `vars/rhel7.yml` / `vars/ubuntu-20.04.yml` first_found
  cases. **dns-config:** dropped the Ubuntu 16.04 `resolvconf` task.
  **bootstrap-python:** dropped the EL7/python2 tasks.
- `.ansible-lint`: skip `role-name` (DeepOps role dirs use hyphens).

## 12. Repo-wide consistency pass  (`c691ab61`, `d28fb847`, `da5cde44`)

A 12-reviewer uniformity audit (98 findings) plus follow-up, applied as minimal
targeted edits across ~40 files: short module names -> FQCN; task-name capitalization
(imperative, acronyms, no trailing periods, OS-disambiguated duplicates); booleans
-> `true`/`false` and dropped redundant `== True`/`== False`; Jinja spacing
(`{{ var }}`, `| default`), `when`-list indent, blank-line separation, key order;
`local_action` -> `delegate_to: localhost`; and a few value typos
(`dashboard_metrics_scraper_tag`, `ssh_max_auth_retries`). yamllint stays at 0
errors and `ansible-lint roles playbooks` is clean except the two
`kubespray_defaults` syntax-checks (the kubespray submodule is not checked out).

## 13. AppArmor unprivileged-userns audit (enroot)  (`b83476fa`)

Web research confirmed the role was already correct: enroot's `.deb` ships a
**scoped** AppArmor profile (`/etc/apparmor.d/enroot`, loaded via `dh_apparmor`)
that re-grants `userns create` to `enroot-nsenter` only, so Ubuntu 24.04's
`kernel.apparmor_restrict_unprivileged_userns=1` stays on. The global sysctl
disable (a user's manual workaround) strips userns hardening host-wide and remains
an opt-in escape hatch (`enroot_apparmor_global_disable`, default false). Expanded
the inline citations (upstream requirements + the Ubuntu 23.10 blog).

## 14. Slurm 25.11.6 config flexibility  (`8247cef2`, `e987d556`, `5db2a738`, `2c74689e`)

The four `etc/slurm/*` templates in the role (slurm.conf, cgroup.conf, gres.conf,
slurmdbd.conf) are kept at the **upstream DeepOps baseline** -- an earlier overhaul
that hard-coded site tunables into them was reverted (`2c74689e`) so the in-tree
templates stay generic and merge-clean. Full per-file site control is instead via
**complete templates under `config/files/slurm/`**, selected by the
`slurm_*_conf_template` vars (`slurm_cgroup_conf_template`, `slurm_gres_conf_template`,
`slurm_dbd_conf_template`), which default to the baseline templates and can be
repointed at the git-untracked `config/` overlay.

- **slurm.conf** stays on the baseline structure: `TaskPlugin=affinity,cgroup`;
  `PrologFlags=Alloc,Serial` (+ `Contain` when `slurm_contain_ssh`);
  `SelectType=select/cons_tres` with `CR_Core_Memory,CR_CORE_DEFAULT_DIST_BLOCK,CR_ONE_TASK_PER_CORE`;
  multifactor priority left commented; `ReturnToService` and `HealthCheckProgram`/
  `HealthCheckNodeState=IDLE` from the existing vars; `AccountingStorageTRES=gres/gpu`
  when `slurm_manage_gpus`.
- **cgroup.conf:** baseline `CgroupAutomount=yes` + `ConstrainCores/Devices/RAMSpace`.
- **gres.conf:** `AutoDetect=nvml` when `slurm_autodetect_nvml`, else the
  per-GPU manual fallback derived from node topology facts.
- **slurmdbd.conf:** baseline `DbdHost=localhost`, `StorageType=accounting_storage/mysql`,
  the example archive/purge comments, `DebugLevel=4`.
- **Flexibility:** the `slurm_*_conf_template` vars are the full-file escape hatch
  for sites that need custom node/partition/gres lines or scheduling policy.
  Documented in `config.example/group_vars/slurm-cluster.yml` and the
  [Slurm guide](docs/slurm-cluster/README.md), incl. the private-config-repo workflow.

## 15. Ported from a production-tested reference deployment

Selectively merged the good parts of a production-tested Ubuntu Slurm
cluster, generalizing anything server-specific into config:

- **`job_submit.lua`** (net-new): a generalized, Jinja-parameterized port of a
  real GPU-type->partition routing plugin. Site config (CPU partitions,
  gpu-type->partition map, default type/partition) comes from Ansible vars, so it is
  a safe no-op with the empty defaults. Improvements over the source: respects an
  explicit `--partition`, also reads modern `--gpus` (`tres_per_node`/`tres_per_job`),
  nil-guards parsing, and writes a **bare** `gpu:<type>:N` gres (the source wrote the
  invalid `gres/gpu:` TRES name). Deployed by `controller.yml` only when
  `slurm_job_submit_plugins` includes `"lua"`.
- **Scheduling/accounting policy** (fairshare, QOS preemption,
  `AccountingStorageEnforce`, typed `AccountingStorageTRES`): adopted as documented,
  opt-in-where-risky tunables rather than hardcoded site values (section 14).
- **slurmdbd archive/purge** defaults: adopted as the commented `Purge*After` /
  `Archive*` recommendations.
- **`prolog.d/50-all-enroot-dirs`:** dropped the parent-dir `chmod 0755` (it
  was removed in production -- `mkdir -p` already creates it 0755 and the
  parent may be a shared/systemd-managed path).
- **Server-specific values NOT copied** (NodeName hardware lines, NodeAddr, specific
  Gres types, user allowlists, NFS exports, hpcsdk versions): these stay as
  placeholders / `*_raw` overrides the operator fills in under `config/`.

## 16. Prerequisite-package modernization  (`a20f1670`)

Re-read each project's current install docs and fixed outdated/breaking prereqs:

- **Slurm (Ubuntu):** `libmariadbclient-dev-compat` was **removed on Ubuntu
  24.04/26.04** (apt failure) -> dropped it and kept `libmariadb-dev` for the
  MariaDB client dev headers (NOT `default-libmysqlclient-dev`, which pulls MySQL's
  `libmysqlclient-dev` and Conflicts/Breaks an installed `libmariadb-dev`); dropped
  unused `ruby-dev`; `python3-minimal` -> `python3`.
- **Slurm (both OS):** moved the slurmrestd-only headers into a new
  `slurm_slurmrestd_deps` installed only when `slurm_build_slurmrestd=true` -- this
  keeps EL10 working (`http-parser-devel` is EPEL-only on EL8/9 and absent on EL10)
  and stops installing REST-API deps the default build never uses.
- **pyxis:** added an explicit compiler dep (`build-essential` / `@Development Tools`).
- **nvidia-container-toolkit:** `gnupg` -> `gnupg2` to match the install guide.

## 17. MOFED -> DOCA-OFED  (`73c627e8`)

The `mofed` role pinned MLNX_OFED 5.6 (2022) and downloaded from the dead
`www.mellanox.com` host (404 on every supported OS). MLNX_OFED's last standalone
release was the 24.10 LTS; its successor is **DOCA-OFED**. Rewrote the role to add
NVIDIA's DOCA network repository (release-aware `ubuntu<ver>` / `rhel<major.minor>`,
`x86_64`/`arm64-sbsa`) with the Mellanox GPG key and `apt/dnf install doca-ofed`
(idempotent; optional reboot), dropping the tarball + `mlnxofedinstall` build path.
**Validation required:** DOCA pins driver/firmware per release and not every OS/arch
is published for every version, so the operator confirms `mofed_repo_base_url` /
version for their adapter+OS at developer.nvidia.com/doca-downloads -- untestable
here without IB hardware. `roce_backend`'s bundled MLNX_OFED 4.7 / Ubuntu 18.04 ISO
is flagged legacy and points at the `mofed` role.

---

## 18. Production optimizations + NFS random-IO tuning  (`520d0932`, `ec14c854`)

A second, exhaustive file-by-file pass over a production-tested reference
cluster's working-tree edits classified every change as
*port* (general optimization), *skip* (server-specific), or *already have*. The 19
general optimizations were ported and generalized; server-specific values (NodeName
hardware, real inventory, usernames, `/data0x` paths) stay as placeholders.

- **Versions/idempotency:** hwloc 2.5.0->2.12.2, pmix 3.2.3->3.2.5; the pmix
  "already installed?" check now reads `pmix_info` (the old `tr -d 'x0'` hex parse
  always rebuilt); slurmdbd controller uses `python3-pymysql` + `mysql_user` over
  the local unix socket (OS-aware path) with `column_case_sensitive`; the
  slurm-exporter stop/restart only fires when the service is actually running.
- **Robustness:** pyxis copies (not symlinks) the enroot hooks; grafana HTTP port
  is configurable; slurm-exporter bind-mounts are `:ro` and add `sshare`;
  node-exporter restarts on its endpoint-config change; nis ypbind `daemon_reload`;
  the dcgm-exporter playbook honours `slurm_cluster_install_nvidia_driver`; a
  deprecated `{{ }}`-wrapped `when:` was unwrapped; spack/nvhpc gained selective-run
  tags; new `config.example/playbooks/ufw-disable.yml`.
- **NFS random-IO bottleneck** (a single shared NFS head under many GPU clients,
  web-researched with citations): client mount options `async,vers=3` (the `async`
  was a silent no-op client-side, `vers=3` a downgrade) ->
  `rw,hard,vers=4.2,nconnect=8,rsize=1048576,wsize=1048576,...`; a new
  `nfs_server_threads` (then a flat 32) writes `/etc/nfs.conf` `[nfsd] threads` (the
  Linux default of 8 starves many clients); and a guardrail to keep enroot
  cache/data/runtime on node-local NVMe. (Section 28 later re-bases these defaults on
  a 100GbE+ fabric -- nconnect 16 + ESnet sysctls, auto-sized nfsd threads; the
  auto-sizing was since dropped for a fixed default of 64 set in `config/`, since
  high auto-sized counts could fail to start with ENOMEM on busy servers.) See
  `docs/slurm-cluster/slurm-nfs.md`.
- **Private-config management** (`docs/deepops/managing-cluster-config.md`): the
  recommended `config.example` (public placeholders) -> gitignored `config/` as its
  own private repo -> `ansible-vault` workflow, and how to pull upstream updates
  without committing site config.

## 19. Final multi-agent verification round  (`c7063698`)

An 18-agent final sweep (deepops<->reference-config parity, per-program docs compliance,
login/compute idempotency + reboot persistence, and a consistency/corruption
file-by-file scan over 661 files) surfaced 8 critical issues, all fixed:

- **requirements.yml / community.mysql, authentication gating, idempotency, reboot
  persistence, lint-corruption recovery** (below). (The earlier
  `AccountingStorageUser` removal no longer applies: `2c74689e` reverted slurm.conf
  to the upstream baseline, which retains `AccountingStorageUser={{ slurm_db_username }}`.)
- **requirements.yml:** added `community.mysql` (the `mysql_user` task had no
  collection declared, so it would fail to resolve on a clean install).
- **authentication.yml:** each directory role (move-home-dirs/kerberos/nis/autofs)
  now runs only when its config vars are defined, so the default run is a clean
  no-op instead of tripping each role's "variable not defined" assert.
- **Idempotency:** slurmd uses `state: started` (+ handler-driven restarts) instead
  of restarting every run; pyxis gates build/install on a stat of the installed
  `spank_pyxis.so` so re-runs no longer rebuild and bounce slurmd.
- **Reboot persistence:** MariaDB is boot-enabled on Debian too (was RedHat-only).
- **Linting corruption recovered (16 files):** the auto-format passes had mangled
  `printf '#!/bin/sh'` -> `'$!/bin/sh'` in six molecule `prepare.yml` files (breaking
  the policy-rc.d shim), and left jinja-spacing/`{{ share_dir}}`/typo artefacts;
  all restored. This is why every later change is re-scanned for corruption.

## 20. CUDA toolkits as Lmod modules -- `nvidia_cuda_toolkit` role  (`3c026a67`, `94fa1419`)

New in-tree role (originally `cuda_toolkits`, renamed to `nvidia_cuda_toolkit` for
naming consistency with the other `nvidia_*` roles -- the rename was kept to this
NEW role only, leaving upstream role names untouched). It installs the latest patch
of each CUDA minor (11.8 .. 13.3) from the official NVIDIA `.run` files into a
shared `/sw/cuda/<version>` tree and generates one Lmod modulefile per version, so
users `module load cuda/<ver>` on any node. The host driver is never touched
(`--toolkit` only). Idempotent (stat `nvcc` per version), opt-in (default off),
installs once on the NFS server. This is the modules-based alternative to the
system-wide `nvidia_cuda` role; with it, `slurm_cluster_install_cuda` is set false.
Playbook: `playbooks/slurm-cluster/nvidia-cuda-toolkit.yml`.

## 21. profile.d / Lmod best-practice overhaul  (`a43200bc`, `1b55c3f8`, `cc74da53`)

- **Lmod init is left to the distro; the role only sets `LMOD_SITE_MODULEPATH`.** The
  `lmod` role no longer ships a custom `z00_lmod.{sh,csh}` (which hard-set `MODULEPATH`
  and sourced `init/bash` directly). It now deploys `00-modulepath.{sh,csh}`, which
  only export `LMOD_SITE_MODULEPATH={{ sm_module_path }}` (Lmod's official site hook).
  The distro Lmod package's `/etc/profile.d/lmod.sh` does the `module`-command init and
  prepends every `LMOD_SITE_MODULEPATH` entry to `MODULEPATH` (preserved across
  `module reset`). The `00-` prefix sorts before `lmod.sh` so the variable is set first.
- **Site-registration snippets unified** on the `00-*` + `LMOD_SITE_MODULEPATH`
  pattern (sorted before `lmod.sh`): `nvidia_cuda_toolkit` ships **no** modulepath
  snippet at all (the shared tree `sm_module_path` is registered once by the `lmod`
  role, and `cuda_modroot` defaults to `sm_module_path`); nvhpc ships
  `00-nvhpc-modulepath.{sh,csh}` (modules mode, `hpcsdk_install_as_modules`) or
  `z95_nvhpc.{sh,csh}` (in-path/compiler-PATH mode, `hpcsdk_install_in_path`);
  rootless-docker ships `00-rootlessdocker-modulepath.{sh,csh}` (was
  `z96_rootlessdocker_modules.sh`).
- **easy-build `z01_eb.{sh,csh}`** now only set the `EASYBUILD_*` env (prefix +
  modules tool); the build-time `module purge`/`module load EasyBuild` is done by the
  build step, not a login script. **sh/csh parity** for every profile.d script.
Grounded in lmod.readthedocs.io/.../090_configuring_lmod.html and Lmod's init/profile.in.

## 22. MPI stack -- PMIx 5.0.10 + OpenMPI 5.0.10  (`16226705`, `075c9489`)

PMIx 3.2.5 -> **5.0.10** (current stable OpenPMIx; Slurm 22.05+ supports v2-v5, so
25.11 links it cleanly). OpenMPI 4.0.3 (EOL) -> **5.0.10**. The OpenMPI role default
keeps `--with-pmix=internal` (5.0.x bundles PMIx 5.x = the same major Slurm links,
wire-compatible, builds anywhere); `config.example` documents the SchedMD
best-practice external-PMIx build (`--with-pmix=<prefix>`, dropping the v5-removed
`--with-pmi`/`--with-slurm`). Sources: slurm.schedmd.com/mpi_guide.html.

## 23. Idempotency + molecule-CI hardening  (`402db12f`, `0d462604`, `8ecdc6d4`, `b06d697b`, `3e0b9e51`, `5c3b232f`)

A file-by-file idempotency audit fixed every re-run-unsafe task: netapp-trident
(`helm install` -> `upgrade --install`; backend create tolerates "already exists";
unarchive `creates:`), ood-wrapper (semanage shell-outs -> idempotent
`community.general.seport`/`selinux_permissive`), cachefilesd / nfs-firewalld /
spack (change-gated restarts + `changed_when`), nis_client (OS-aware service name,
proper domain file). Three molecule-CI breakages fixed (nfs sysctl + cachefilesd
restart skipped in containers; openmpi default reverted to internal PMIx so the
converge builds). **`nvidia-dcgm` is now GPU-gated** -- it had no gate, so the dcgm
service-start failed on a 0-GPU slurm-node (the one task that broke a default run on
a CPU-only node); CPU-only nodes now complete cleanly. Every other GPU role was
already correctly gated on `ansible_local['gpus']['count']`.

## 24. Generality, config overlay, and install defaults  (`a3aaffdb`, `f81b4623`, `5c3b232f`)

Scrubbed real-looking NetApp creds from the committed example; made the logging port
overridable; documented the new tunables (`nfs_server_threads`, `nfs_tune_network`,
`grafana_port`, `locale_lang`) and de-staled the spack example. The gitignored
`config/` overlay is built to replicate a real site's cluster (real inventory,
full-file Slurm templates under `config/files/slurm/` via `slurm_*_conf_template`,
NFS exports/mounts, job_submit routing, ports) and diffed against `config.example`.
Default install posture set to:
driver **on** (branch 595, GPU-gated), system CUDA **off** (served as Lmod modules),
HPC SDK **off**, spack build **off**. node_exporter now mounts the host rootfs
(`--path.rootfs=/host`) so its filesystem metrics describe the node, not the container.

## 25. Dead-code removal + disambiguation guide  (`e7495ca3`)

A duplication/overlap sweep found no true duplicates. Removed the only orphan +
self-superseded role (`nvidia-gpu-operator-node-prep`, handled by GPU Operator
>=1.9) and the redundant ntpd path (`ntp-client.yml` + `geerlingguy.ntp`; chrony is
the default). Added `docs/deepops/choosing-roles-and-playbooks.md` to disambiguate
the similar-looking-but-distinct pairs (nvidia_cuda vs nvidia_cuda_toolkit, the three
registries, the module-build systems, dcgm agent vs exporter, the two gpu-clocks
playbooks, mofed vs roce_backend, chrony) -- the real fix for "which one do I use?"
without merging distinct components.

## 26. Repo housekeeping  (`372b808d`, branch rename)

The working branch was renamed `modernize` -> **`main`** and set as the GitHub
default; `master`/`modernize` were removed from the fork; the `upstream` remote
(github.com/NVIDIA/deepops) is configured for periodic
`git fetch upstream && git merge upstream/master`. Documentation de-staled:
versions (OpenMPI/PMIx/Ansible), driver var names (`nvidia_driver_branch`,
`nvidia_driver_kernel_modules`, `nvidia_driver_reboot`), config paths
(`k8s_cluster.yml`), removal of the Helm-2/Tiller manual-install block, and broken
relative links.

## 27. Version-currency sweep  (`2fc74b55`, `b34c2c0f`)

Web-verified every pin (2026-06-08). **Updated:** NGC ready-containers (pytorch
24.04->26.05, cuda 12.4.1->13.2.1-ubuntu24.04, tensorflow ->25.02 -- the FINAL NGC
TF release), hwloc 2.12.2->2.13.0, gpu-operator chart v26.3.2, k8s device-plugin +
GFD 0.19.2, nfs-client-provisioner 4.0.18, HPC SDK 23.7->26.3 (cuda 13.1),
SingularityCE 3.11.4->4.4.2 (+ Go 1.26.4). **Already current (confirmed):** slurm
25.11.6, pmix 5.0.10, openmpi 5.0.10, enroot 4.2.0, pyxis 0.24.0,
container-toolkit 1.19.1, dcgm-exporter, prometheus/alertmanager/node-exporter, nhc,
spack, mofed/DOCA 3.3.0. **Deferred (major reworks -- documented in each role's
defaults, need install-flow rework + validation, not blind bumps):** Open OnDemand
2->4, NetApp Trident 21->26, k8s-registry chart 2->3 (did fix the deprecated
`helm.twun.io` repo URL), k8s dashboard v2->v7.

## 28. NFS re-based on a 100GbE+ fabric  (`7a654c8e`, `276dc245`)

The general default now assumes a 100GbE+ (B200-class) fabric: client `nconnect`
4->16 and the role's socket-buffer sysctls bumped to ESnet Fasterdata 100G values
(rmem/wmem_max 2 GiB-1, 1 GiB tcp autotune ceiling, + netdev_max_backlog/optmem_max/
tcp_mtu_probing/default_qdisc=fq/somaxconn). These are safe ceilings (TCP autotunes),
so fine on 10/25GbE too -- lower `nconnect` there. Exports gained `no_subtree_check`
(modern recommended default); `sync` exports vs `async`-only-for-scratch documented.
Values verified against ESnet, NetApp ONTAP, RHEL, and NVIDIA DGX BasePOD docs.

## 29. Slurm: bring-your-own job_submit + GPU billing + generalized examples  (`0cfa77f6`, `08272e81`, `2fc74b55`)

`job_submit.lua` is no longer generated from routing vars -- users supply their own
(`config.example/files/slurm/job_submit.lua` is a starting point) and the role
copies it **verbatim** (`copy`, not `template` -- lua `{{` table literals would break
Jinja) with an assert when the lua plugin is enabled. config.example
node/partition/gres + tunables examples generalized to modern 8-GPU nodes (B200
baseline, comments kept general); the partition `MaxTime` example (config.example
`slurm_max_job_timelimit`) uses the documented `UNLIMITED` keyword (the role's
baseline template default is `INFINITE` when the var is unset).

## 30. enroot CLI permissions  (`20dca8f0`)

enroot creates each user's RUNTIME/CACHE/DATA leaf by `mkdir`-ing **as the user**, so
the parents must be writable. deepops only made the per-user leaf in the Slurm prolog,
so pyxis-under-srun worked but **interactive `enroot` failed** ("mkdir /run/enroot:
Permission denied", NVIDIA/enroot#23), recurring each reboot (`/run` is tmpfs). Fix
(ports the reference deployment's approach): the enroot role ships a tmpfiles.d creating the
sticky-1777 parents (`/run/enroot`, `/var/lib/enroot-cache`, `/tmp/enroot-data`), and
the per-user paths + a correct `enroot.conf` are now role defaults (were only in the
example group_vars, so a bare deploy used to put cache/data on the NFS home). Kept
deepops's already-correct scoped-AppArmor + userns sysctls + prolog/epilog lifecycle.

## 31. NGC docker daemon defaults + slurmrestd JWT  (`4a106d37`, `fe5b947f`)

Ported the daemon.json shm/ulimit tuning the comparison surfaced: NGC/NCCL bare
`docker run` needs `default-shm-size=1G` + `default-ulimits` memlock=-1 /
stack=67108864 (docker's 64 MiB shm breaks multi-GPU NCCL) -- the exact values are
NVIDIA's (Frameworks / Triton / DeepLearningExamples docs). Merged into daemon.json
before `nvidia-ctk` (idempotent). Added `libjwt` to the slurmrestd build deps
(`rest_auth/jwt`).

## 32. Docs + housekeeping  (`99b9190d`, `5041a3f5`, `feb606cf`, `d230ee0b`)

Added `docs/deepops/config-defaults-vs-upstream.md` (config.example defaults vs the
fork point). Dropped the unused `geerlingguy.ntp` galaxy dependency (matching the
docs). Documented the deferred OOD/Trident/registry major bumps in their role
defaults. Removed `scripts/deepops/config-diff.sh` by preference, plus
assorted doc cleanups.

## Status

Every item from the modernization brief is implemented, linted (yamllint 0;
ansible-lint **production** clean except the two `kubespray_defaults` syntax-checks
that need the kubespray submodule checked out), and verified by repeated multi-agent
file-by-file review.

**Tooling currency (some pins held on purpose):** yamllint is bumped to **1.38.0**,
but **ansible-lint stays at 26.1.1** and **pre-commit-hooks at v5** -- the newer
ansible-lint (26.4.0) adds ~11 schema / yaml-strictness failures, so those are held
for CI stability and tracked separately. The runtime **Ansible major (10->14)** is
coupled to the pinned kubespray (kubespray pins ansible-core ranges), so it moves
only together with a kubespray bump. In short: ansible-lint 26.4.0, pre-commit-hooks
v6, and Ansible 14 are deliberate, tested upgrades for later -- not blind bumps now.

Other caveats: the DOCA-OFED migration (section 17) needs RDMA/IB hardware to validate.

Deferred by design (each needs an install-flow rework + validation on a real
cluster, not a blind bump -- documented in the relevant role's defaults): Open
OnDemand 2->4, NetApp Trident 21->26, k8s-registry chart 2->3, k8s dashboard
v2->v7.

> Maintenance note: keep this file current. When a change lands, add or amend the
> relevant section (what / how / why + commit) and refresh the Status -- do not let it
> go stale.
