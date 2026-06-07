# DeepOps Modernization — Change Log (branch `modernize`)

This document explains **every change made on the `modernize` branch since the
forked NVIDIA DeepOps baseline** (commit `8858399d`, "release 26.05"): *what* was
changed, *how*, and *why*. It is meant for review before merging.

- **Goal:** keep this fork as a clean, public, continuously-maintained DeepOps —
  latest pinned versions, official-docs-compliant (with citations in code),
  consistent/linted, idempotent + boot-persistent.
- **Target OS matrix:** Ubuntu 22.04 / 24.04 / 26.04 + RHEL-family 8 / 9 / 10
  (RHEL / Rocky / Alma).
- **Verification:** every change validated with `yamllint` (0 errors) and
  `ansible-lint roles/` (0 failures, **production** profile). The whole tree was
  additionally reviewed file-by-file against the baseline (2 rounds, converged to
  0 problems) and against the OS matrix (80 files).
- **Diff vs baseline:** ~404 files, +3988 / −2985 (the bulk is mechanical
  FQCN / naming / formatting normalization — see §1).

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
  play/handler names; `yes/no` → `true/false` (363); quoted octal modes (62);
  jinja spacing; task key ordering; trailing-whitespace / document-start / CRLF.
- Fixed a duplicate key in `config.example/nvidia-mig-config.yml`.
- Documented the Ansible Vault + private-config-repo workflow in
  `docs/deepops/configuration.md`.

**Why:** The baseline mixed `yes/no` and `true/false`, missing FQCNs,
inconsistent task naming/quoting, trailing whitespace, etc. Consistency was the
top priority. A few **corruption issues introduced by the automated fixer** were
caught and repaired (see §8): a Jinja filter mis-capitalized (`default`→`Default`)
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
- CUDA: `nvidia_cuda_install` (default true) gates the toolkit; the slurm flow
  keeps `slurm_cluster_install_cuda`.

**Why:** The old path listed **both** the dkms and no-dkms `-open` packages and
the Galaxy role installed each package in a separate `apt` loop → no coherent
kernel module built → `nvidia-smi` failed. The old open-module path was also
gated off by default, and RHEL had **no** open-module support at all. The
single-meta approach matches the manual `apt install nvidia-driver-580-server-open`
that is known to work. *(Note: a follow-up docs audit of post-install actions —
`nvidia-persistenced` etc. — is in progress; see §9.)*

---

## 3. Container / runtime stack

### 3a. NVIDIA Container Toolkit + docker boot-enable  (`76f7a09a`)
- **Pin** the unified toolkit package set (`nvidia-container-toolkit` + base +
  `libnvidia-container-tools` + `libnvidia-container1`) to `1.19.1-1`
  (`nvidia_container_toolkit_version`, empty = latest).
- **Enable docker at boot** (`nvidia_container_toolkit_enable_docker`): on RHEL
  `docker-ce` does not auto-enable, so docker-based services (exporters, registry)
  were dead after reboot — this closes the top boot-persistence gap.
- Route **all** supported OSes (Ubuntu 22.04+/EL 8+) through the native toolkit
  role and **remove the EOL `nvidia.nvidia_docker`** (nvidia-docker2) fallback.
- Fixed the `docker_install | default('yes')` truthy bug (always truthy →
  `default(true) | bool`).
- **Why:** `nvidia-docker2`/`nvidia-container-runtime` are end-of-life; the
  unified toolkit + `nvidia-ctk runtime configure` is the current path.

### 3b. enroot 4.2.0 in-tree  (`47213d15`)
- New `roles/enroot` installs **enroot 4.2.0** from the release `.deb`/`.rpm`
  (version-pinned, with a remove/reinstall step for clean upgrades).
- Sets `user.max_user_namespaces` / `user.max_mnt_namespaces` (hard prerequisite).
- **Ubuntu 23.10+/24.04 AppArmor:** relies on enroot's **bundled scoped AppArmor
  profile** (loaded via handler) instead of globally disabling
  `kernel.apparmor_restrict_unprivileged_userns` — a much smaller security blast
  radius. A global-disable escape hatch is available for trusted nodes.
- RHEL: enables unprivileged userns on the kernel cmdline (grubby) if missing.
- Per-user runtime/cache/data dirs are created `0700` by the existing Slurm
  prolog (not world-writable tmpfiles).
- **Why:** the old `nvidia.enroot` v0.5.0 was an EOL 3.x release with no handling
  for the unprivileged-userns restriction modern Ubuntu enables by default —
  this is the "enroot fails after install on Ubuntu 24.04" problem. Verified
  against the official enroot docs: `+caps` does **not** remove the userns
  requirement (caps apply only to image-import helpers), so the scoped AppArmor
  profile is the correct, standard fix.

### 3c. pyxis 0.24.0  (`32c040f9`)
- Bump pyxis 0.11.1 → **0.24.0**, rebuilt against the deployed Slurm headers
  (pyxis is ABI-bound to Slurm — must be rebuilt on a Slurm bump).
- Add `make install` (plugin at `{{ slurm_install_prefix }}/lib/slurm/spank_pyxis.so`).
- Parameterize `plugstack.conf.d/pyxis.conf` (runtime_path, execute_entrypoint,
  container_scope, sbatch_support, `use_enroot_load=1`).

---

## 4. SLURM 25.11.x compliance  (`5ddd33a3`, `4922b2cb`)

- **cgroup.conf → cgroup/v2:** `CgroupPlugin=autodetect`, add `ConstrainSwapSpace`,
  drop the removed `CgroupAutomount` and v1-only Kmem keys.
- **build deps:** add the cgroup/v2 (`dbus`, `bpf`) and REST-API (`json-c`,
  `http-parser`, `yaml`) libraries plus NUMA, Lua, readline for Ubuntu and EL.
- **slurm.conf:** cite the 25.11.6 docs/configurator; canonical
  `TaskPlugin=task/affinity,task/cgroup`. (Already free of the removed
  `CryptoType`/`FastSchedule`/`cons_res`.)
- **slurmdbd.conf / gres.conf:** add 25.11.6 doc-reference headers; already
  compliant (`auth/munge` + `accounting_storage/mysql`; `AutoDetect=nvml`).
- **Why:** cgroup/v1 is deprecated in 25.11 and `dbus`/`bpf` are build-time
  requirements for the v2 plugin; the REST/Lua libs were missing.
- **Refs:** `https://slurm.schedmd.com/archive/slurm-25.11.6/{cgroup.conf,quickstart_admin,slurm.conf,slurmdbd.conf,gres.conf}.html`

---

## 5. Monitoring stack  (`dfab757d`)

- **Uniform systemd hardening** on all six docker-based units (prometheus,
  grafana, alertmanager, node-exporter, slurm-exporter, dcgm-exporter): order
  after `network-online.target` (so the image pull doesn't race the network on
  boot) and add `RestartSec=10` (avoid crash-loop hammering).
- **Version bumps:** prometheus `v3.11.3`→`v3.12.0`, alertmanager `v0.32.1`→
  `v0.32.2`, grafana `13.0.1`→`13.0.2`. node-exporter `v1.11.1` and dcgm
  `4.5.3-4.8.2-distroless` already current.
- **slurm-exporter:** use the maintainer image `12kimih/prometheus-slurm-exporter`
  (documented to pin a versioned tag in production).

---

## 6. OS matrix support — Ubuntu 22.04/24.04/26.04 + EL 8/9/10  (`2acc8e8e`, `0ab38774`, `6f2955c0`, `3169f2f9`)

A file-by-file review of 80 OS-conditional files found and fixed real breakage:

- **EL9/10 `crb`:** the CodeReady Builder repo was renamed from `powertools`
  (EL8) to `crb` (EL9+). Hardcoding `powertools` broke the Slurm build, Lmod, and
  Singularity deps on EL9/10. Now derived from the major version.
- **dns-config:** systemd-resolved was only disabled on Ubuntu 16/18/20 → static
  DNS never applied on 22.04/24.04/26.04. Now disabled on 18.04+ and the resolv.conf
  stub symlink is replaced before templating.
- **nis_client:** the restart handler was gated to Ubuntu 14.04 and never fired
  on modern Ubuntu → config changes weren't applied. Now a single `nis` handler.
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
- `playbooks/utilities/disable-acs.yml` — persistent oneshot unit disabling PCIe
  ACS for NVIDIA NCCL GPU P2P (reapplied every boot).
- `playbooks/nvidia-software/nvidia-vulkan.yml` — Vulkan runtime/tools/Mesa.
- `playbooks/utilities/apt-upgrade.yml` — hostlist-parameterized dist-upgrade.
- `playbooks/utilities/reboot.yml` — rolling reboot (`reboot_serial`).

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
  reconciled, `Default`→`default` restored, no token corruption remained).
- A wrong FQCN guess (`ansible.mysql.mysql_user`) was corrected to
  `community.mysql.mysql_user` (canonical on the CI's ansible 10.7.0).
- **Recurrence prevention:** CI + pre-commit run yamllint/ansible-lint on every
  change; long commands should use block scalars; prefer targeted edits over
  whole-file `--fix`.

---

## 9. Known follow-ups (intentionally deferred)

- **NVIDIA driver post-install / install-method** — auditing the official driver
  guide for `nvidia-persistenced` enablement, the cuda-keyring + `nvidia-open`
  (NVIDIA-repo) method vs the Canonical `-server-open` method, and fabricmanager.
  *(In progress — this section will be updated when the role is revised.)*
- **nvidia-dgx** 26.04/EL10 — NVIDIA DGX OS bundles for those aren't published
  yet; the role intentionally fails on an unvalidated DGX OS.
- **mofed** — needs a version bump (or DOCA-OFED) for Ubuntu 24.04+/EL9+.
- **ufw firewall** — port as a parameterized firewall role (the source hardcoded
  subnets/ports).
- **ood-wrapper EL8+** — replace the EL7/SCL (`httpd24`, python2) vars.
