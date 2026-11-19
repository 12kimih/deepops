# The MUNGE key

Slurm authenticates every message with MUNGE, and every Slurm host must hold the same
key in `/etc/munge/munge.key`. DeepOps copies it from the site configuration:
`slurm_munge_key_file` (default `config/files/munge/munge.key`), kept encrypted with
Ansible Vault.

The key is random bytes, as MUNGE's own guide says, never derived from a password: a
derived key is only as strong as the password, and DeepOps once derived it with
Ansible's `password_hash`, whose output follows Ansible's hashing internals.

## A new cluster

Nothing to do. The first play of `playbooks/slurm-cluster.yml`, `slurm-cluster/slurm.yml`
and `slurm-cluster/slurm-upgrade.yml` (`playbooks/slurm-cluster/site-secrets.yml`,
`roles/site_secrets`) creates the file when it is missing: 1024 random bytes from the
operating system's CSPRNG, vault-encrypted with the repository's vault password file
(`ansible.cfg` `vault_password_file`), mode 0600. It never replaces an existing file.
Commit the new file to the site config repository; `--check` only reports it.

## A cluster that already runs MUNGE

Capture its key before the first deploy from this repository, or the first play makes
a new one and the deploy installs it on every host at once. Check that the nodes agree,
copy the key from the controller, and vault it at once:

```bash
cd ~/deepops
uv run ansible slurm-cluster -b -K -m ansible.builtin.command -a "sha256sum /etc/munge/munge.key" | awk '/munge.key/ {print $1}' | sort | uniq -c
uv run ansible 'slurm-master[0]' -b -K -m ansible.builtin.fetch -a "src=/etc/munge/munge.key dest=config/files/munge/munge.key flat=yes"
uv run ansible-vault encrypt config/files/munge/munge.key
```

The first command must print one checksum, and the vaulted copy must match it:

```bash
uv run ansible-vault view config/files/munge/munge.key | sha256sum
```

## Changing the key

A new key must reach every host before munge restarts anywhere: move the file away (the
first play then creates a new one), and deploy to the whole cluster in one run (no
`--limit`) while no jobs run.
