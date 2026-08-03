# Administrator sudo on a Slurm + NIS cluster

Granting sudo through the `sudo` **group** gives two different answers on the same
node depending on how you got there. This document explains why, and how this repo
grants it through a NIS **netgroup** instead.

## The problem

A user's supplementary groups are resolved in two different places:

| Path | Where groups are resolved |
|---|---|
| Slurm job (`srun`, `sbatch`) | the **submit/controller** node, shipped as numeric GIDs (`send_gids`, default since 20.11) |
| Direct SSH login | the **node itself**, via its own `nsswitch.conf` |

So `sudo` group membership on the head node grants sudo *inside jobs* on every compute
node but not over SSH to those same nodes, and membership on a compute node does the
reverse. Whenever the two member lists drift apart — and nothing keeps them in step —
some people hold root inside jobs on every compute node while having no sudo at all
over SSH, and others the other way around. Neither state is visible from looking at
one node.

Making the group consistent is not possible. `sudo` is gid 27 — below `GID_MIN` — and
`/var/yp/Makefile` filters the NIS group map to `GID >= GID_MIN`, so the group can
never be published. Editing `/etc/group` on each node by hand fixes only the SSH path,
because job credentials never consult it.

There is a second, independent thing to check: whether a password can be verified on the
compute nodes at all. If it cannot, a password-requiring sudo rule can never succeed
there no matter what the netgroup says. Confirm by behaviour, as the user, on the node:

```sh
sudo -v        # prompts and returns 0 => a password-requiring rule works here
```

## The fix: a netgroup

A netgroup is matched by **name** through `innetgr(3)`, not by a numeric id. That
sidesteps both the `GID_MIN` filter and the numeric-GID path, so one definition on the
NIS master gives every node the same answer by every path.

1. Publish the netgroup from the NIS master (`config/group_vars/nis-master.yml`):

   ```yaml
   nis_export_netgroups:
     - name: "{{ cluster_sudoers_netgroup }}"
       users:
         - alice
         - bob
   ```

   The `nis_server` role renders `/etc/netgroup` and rebuilds the maps. Membership is
   **declarative** — deleting a name here revokes that person's sudo on the next run.
   (`nis_export_groups`, by contrast, only ever adds.)

2. Name it once in `config/group_vars/all.yml`:

   ```yaml
   cluster_sudoers_netgroup: cluster_admins
   ```

3. `cluster_sudoers` then installs one rule per node:

   ```
   +cluster_admins ALL=(ALL:ALL) ALL
   ```

The rule **requires a password by default** (`cluster_sudoers_nopasswd: false`). Set
`cluster_sudoers_nopasswd: true` only where the `sudo -v` check above fails.

## Safety

Every rule is checked with `visudo -cf` *before* it is installed, and the whole
configuration is re-checked with `visudo -c` at the end of the run. The role also
fails rather than install a rule whose netgroup does not resolve on that node, since
such a rule grants nothing.

Run it against one node first and confirm sudo still works there:

```sh
ansible-playbook -l <one-node> playbooks/generic/cluster-sudoers.yml
```

**Break-glass.** The stock `%sudo ALL=(ALL:ALL) ALL` rule in `/etc/sudoers` is left
untouched, so a **local** account that is in the local `sudo` group *and* has a local
password entry keeps sudo even if NIS is unreachable. Confirm such an account exists on
every node before relying on the netgroup rule. If NIS goes down and no local path
exists, nobody can sudo anywhere.

## Unmanaged drop-ins

`playbooks/bootstrap/bootstrap-sudo.yml` grants permanent passwordless root to
**whoever runs it**, on every host it touches, as `/etc/sudoers.d/<their-name>`:

```yaml
content: "{{ ansible_env.SUDO_USER | default(ansible_env.USER) }} ALL=(ALL) NOPASSWD:ALL"
```

Such a file grants root independently of any group, so `getent group sudo` does not
show it.

`cluster_sudoers` **reports** every drop-in it did not write, on every run. Set
`cluster_sudoers_prune: true` to delete them, after reviewing the reported list.

Two things are never touched:

- **Anything a package owns.** Ownership is asked of `dpkg`/`rpm` rather than matched
  against a list of names, so a drop-in installed by a component (Open OnDemand ships
  one) is covered without being listed.
- **`cluster_sudoers_keep`**, for files no package owns that must survive. The defaults
  are `README`, `root` and `90-cloud-init-users` — the last being the only sudo path on
  a stock cloud image.

The report separates two categories:

| | meaning |
|---|---|
| **active** | no package owns it and sudo honours it |
| **inert** | sudo **skips** it, because the name contains a dot or ends in `~` |

To audit by hand:

```sh
ansible all -m command -a 'ls /etc/sudoers.d/'
```

## Related

- [Docker access from inside Slurm jobs](docker-in-slurm-jobs.md) — the same
  numeric-GID mechanism, solved with a published group rather than a netgroup, because
  the Docker socket needs an actual GID to be owned by.
- `playbooks/utilities/check-id-consistency.yml` — asserts published groups resolve to
  their pinned GID on every node.
