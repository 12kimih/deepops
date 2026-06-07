# Managing private cluster config in a public DeepOps fork

If you maintain a **public** DeepOps fork but run a **private** cluster, your
site‑specific settings (real inventory/IPs, `NodeName=` hardware lines, NFS exports,
usernames, secrets) must never be committed to the public repo. DeepOps is already
wired for exactly this — no `.gitignore` or `ansible.cfg` changes are needed.

How it works (already in the repo):

- `.gitignore` ignores `/config*/` but keeps `!/config.example/` — so `config/` is
  invisible to git while the `config.example/` **template** is tracked.
- `ansible.cfg` points `inventory = ./config/inventory,...` and has a ready‑to‑enable
  `#vault_password_file = ./config/.vault-pass`.
- `scripts/setup.sh` copies `config.example` → `config` only if `config/` is absent.

> **Golden rule:** edit real values only under `config/`. Keep `config.example/`
> generic (placeholders + commented examples). Anything you put in `config.example/`
> **is committed and pushed to the public fork.**

## Recommended pattern: `config/` as its own private repo + ansible-vault

This is DeepOps' documented recommendation and keeps your real config version‑controlled
and backed up while staying invisible to the public fork.

### 1. Create the working config from the template

```bash
cd <deepops>
cp -rfp config.example config        # or run scripts/setup.sh
```

### 2. Make `config/` its own private git repo

```bash
cd <deepops>/config
git init -b main
printf '.vault-pass\n*.vault-pass\nvault-password*\n' > .gitignore   # never commit the vault password
git add -A && git commit -m "Initial private cluster config"
gh repo create my-deepops-config --private --source=. --remote=origin --push
```

The outer DeepOps repo treats `config/` as ignored and never recurses into its `.git`;
the two repos are fully independent.

### 3. Put real data in `config/`

```bash
cd <deepops>/config
vim inventory                      # real hostnames / IPs
vim group_vars/slurm-cluster.yml   # slurm_nodes_raw / slurm_partitions_raw, real NFS exports, ...
vim group_vars/all.yml
git add -A && git commit -m "Real prod cluster" && git push
```

For server‑specific Slurm hardware (NodeName/partition/gres lines), use the
`slurm_nodes_raw` / `slurm_partitions_raw` / `slurm_gres_raw` overrides documented in
[the Slurm guide](../slurm-cluster/README.md#customizing-the-slurm-configuration).

### 4. Secrets via ansible-vault (encrypted values, grep‑able names)

```bash
cd <deepops>
( umask 077; head -c 32 /dev/urandom | base64 > config/.vault-pass )   # or your chosen password
chmod 600 config/.vault-pass
```

Enable it by uncommenting in `ansible.cfg`:

```ini
vault_password_file = ./config/.vault-pass
```

Keep plaintext vars that *reference* vault values, and a separate encrypted file holding
the actual secrets:

```yaml
# config/group_vars/all.yml (plaintext — safe to read/grep)
slurm_db_password: "{{ vault_slurm_db_password }}"
```

```bash
ansible-vault create config/group_vars/secrets.yml   # vault_slurm_db_password: ...
ansible-vault edit   config/group_vars/secrets.yml   # edit later
ansible-vault rekey  config/group_vars/secrets.yml   # rotate the password
```

Commit `config/group_vars/secrets.yml` (ciphertext) to the **private** repo; never commit
`config/.vault-pass`.

### 5. Run exactly as before

```bash
cd <deepops>
ansible-playbook -l slurm-cluster playbooks/slurm-cluster.yml
```

Inventory and the vault password resolve automatically from `ansible.cfg`.

## Pulling upstream DeepOps updates without clobbering `config/`

`config/` is gitignored, so `git pull` / `rebase` / `checkout` physically cannot touch or
stage it — only the `config.example/` template changes upstream.

```bash
cd <deepops>
git remote add upstream https://github.com/NVIDIA/deepops.git   # one-time
git fetch upstream --tags
git diff master <release-tag> -- config.example/   # spot new/changed template params
git merge upstream/master                          # update your fork
```

Then reconcile any new keys into your private `config/group_vars/*.yml` and commit them in
the **private** repo. Because `config/` and `config.example/` are different directories,
upstream template edits never overwrite your live config.

## Alternatives (and why the above is preferred here)

- **Separate inventory repo** (`-i ../site-config/prod/hosts`): cleanest isolation, but two
  repos to wire together; equivalent in spirit to the `config/` overlay, which DeepOps
  already wires for you.
- **git submodule** for `config/`: pins an exact config commit to a code commit, but adds
  clone/update friction and can leak the private repo URL via `.gitmodules`.
- **SOPS / git‑crypt** instead of ansible‑vault: fine if you already use them, but
  ansible‑vault is built in and needs no extra tooling.
- **git subtree**: vendors full history *into* the repo — the opposite of keeping config
  out of a public repo; avoid for this use case.
