# The MUNGE key

Slurm authenticates every message with MUNGE, and every Slurm host must hold the same
key in `/etc/munge/munge.key`. DeepOps copies it from the site configuration:
`slurm_munge_key_file` (default `config/files/munge/munge.key`), kept encrypted with
Ansible Vault.

The key is random bytes, as MUNGE's own guide says, never derived from a password: a
derived key is only as strong as the password, and DeepOps once derived it with
Ansible's `password_hash`, whose output follows Ansible's hashing internals.

## Where the file comes from

Nothing to do by hand. When the file is missing, the first play of
`playbooks/slurm-cluster.yml`, `slurm-cluster/slurm.yml` and
`slurm-cluster/slurm-upgrade.yml` (`playbooks/slurm-cluster/site-secrets.yml`,
`roles/site_secrets`) looks for `/etc/munge/munge.key` on every Slurm host:

- **A cluster that already runs MUNGE:** the play adopts the key the hosts hold. It
  compares their SHA-256 checksums and, when they agree, reads the key from the first
  controller (`slurm-master[0]`) into the site configuration. When they differ it stops,
  listing each host's checksum, and writes nothing: make the hosts agree (copy the key
  the cluster should keep to the others, restart munge there), then run again.
- **A new cluster** (no host holds a key): the play creates 1024 random bytes from the
  operating system's CSPRNG.

Either way the file is vault-encrypted with the repository's vault password file
(`ansible.cfg` `vault_password_file`), mode 0600, and an existing file is never
replaced. Commit it to the site config repository; `--check` only reports what would be
adopted or created.

## Changing the key

Replace the file on purpose (moving it away is not enough: the play would adopt the key
the hosts still hold), then deploy to the whole cluster in one run (no `--limit`) while
no jobs run, so the new key reaches every host before munge restarts anywhere:

```bash
head -c 1024 /dev/urandom | uv run ansible-vault encrypt --output config/files/munge/munge.key -
```
