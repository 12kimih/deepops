# The MUNGE key

Slurm authenticates every message with MUNGE, and every Slurm host must hold the same
key in `/etc/munge/munge.key`. DeepOps copies it from the site configuration:
`slurm_munge_key_file` (default `config/files/munge/munge.key`), kept encrypted with
Ansible Vault. A deploy stops if the file is missing.

The key is random bytes, as MUNGE's own guide says, never derived from a password: a
derived key is only as strong as the password, and DeepOps once derived it with
Ansible's `password_hash`, whose output follows Ansible's hashing internals.

## A cluster that already runs MUNGE

Keep the key the nodes have, or every node must change at once. Check that they agree,
copy it from the controller, and vault it at once:

```bash
cd ~/deepops
uv run ansible slurm-cluster -b -K -m ansible.builtin.stat -a "path=/etc/munge/munge.key checksum_algorithm=sha256" | grep -o '"checksum": "[^"]*"' | sort | uniq -c
uv run ansible 'slurm-master[0]' -b -K -m ansible.builtin.fetch -a "src=/etc/munge/munge.key dest=config/files/munge/munge.key flat=yes"
uv run ansible-vault encrypt config/files/munge/munge.key
```

The first command must print one checksum, and the vaulted copy must match it:

```bash
uv run ansible-vault view config/files/munge/munge.key | sha256sum
```

## A new cluster

```bash
mkdir -p config/files/munge
mungekey --create --keyfile config/files/munge/munge.key   # from the munge package
uv run ansible-vault encrypt config/files/munge/munge.key
```

`dd if=/dev/urandom bs=1 count=1024 > config/files/munge/munge.key` makes an equivalent
key where `mungekey` is not installed.

## Changing the key

A new key must reach every host before munge restarts anywhere: replace the file, vault
it, and deploy to the whole cluster in one run (no `--limit`) while no jobs run.
