# Accounts: the people roster, NIS or LDAP

Users and their access come from one roster, `people.yml` next to the inventory
(`config.example/people.yml`). A deploy turns it into accounts in one directory, which
every node reads:

| | NIS | LDAP |
|---|---|---|
| Server | `roles/nis_server` on the `[nis-master]` host | `roles/ldap_server` (OpenLDAP) on the `[ldap-server]` host |
| Accounts | the server's local files, made by `roles/people` | entries made from the roster on every run |
| Client | `roles/nis_client` (ypbind) | `roles/sssd_client` (SSSD) |
| Passwords | the server's `/etc/shadow` | the directory (`passwd` on any node changes it) |
| On the wire | NIS, unencrypted; maps any host in `securenets` can read | StartTLS with the cluster's own CA; nothing to anonymous binds |
| Server down | no lookups | nodes answer from SSSD's cache; cached users log in |

Settings (AUTH in `config.example/group_vars/all.yml`):

- `directory_service`: what each host reads accounts from: `nis`, `ldap` or `none`.
  It defaults to `nis` when `nis_domain` and `nis_server` are set. It can differ per
  host while a cluster moves from one directory to the other.
- `nis_domain` and `nis_server` turn NIS on. `ldap_suffix` (`dc=example,dc=internal`)
  turns LDAP on.
- `directory_groups` and `directory_netgroups`: the cluster groups (the Docker socket
  group) and netgroups (administrator sudo) every directory publishes. Members come from
  the roster. Each collaboration folder of `shared_projects` adds its group to them
  (`directory_groups_all`, [shared-space.md](../slurm-cluster/shared-space.md)).
- The roster's `reserved_logins`: names no person may have, such as the shared space's
  directory under `/home`; the roster check refuses them.
- `ldap_readers`: the read-only accounts that search the directory. `sssd` is the
  nodes'; add `dex` for Open OnDemand's login. Their passwords are files under
  `ldap_secrets_dir` (`files/ldap/<reader>.password`). The first play of
  `authentication.yml` makes them when missing (`roles/site_secrets`, vaulted), so
  nobody types or chooses them. If a file is lost, a new one is made, and the directory
  and the clients take the new password on the next run.

Every directory is a component (`docs/deepops/component-state.md`) with
`removal: explicit`. A switch or a group never takes one away, because that would lock
its users out. A site retires one by naming it in `deepops_component_removals`.

## The LDAP directory

The tree is RFC 2307: what NIS served, one to one, so the move keeps every UID and GID
(`plugins/filter/directory.py`).

| Branch | Entries |
|---|---|
| `ou=people` | `uid=<login>`: the roster UID, GID = UID, home, shell. Someone who has left keeps the entry, so the UID stays reserved. The entry is expired (`shadowExpire: 1`), with a nologin shell and no password, so SSSD refuses every login, SSH keys included. |
| `ou=groups` | the per-user groups (GID = UID) and `directory_groups_all` (`directory_groups` and the collaboration folders' groups), members as `memberUid` |
| `ou=netgroups` | `directory_netgroups` as `nisNetgroupTriple` `(,user,)` |
| `ou=services` | the read-only accounts |

Each run makes the tree match the roster (`roles/ldap_server/library/ldap_tree.py`).
Under groups, netgroups and services, anything the roster does not declare is removed.
People are never removed. An account the roster does not list stops the run, so the
roster gains it as `status: left`.
Someone leaving has `ends:` (their last day) and keeps logging in for
`people_leave_grace_days` (60, `cluster_user_stale_days`) more days: the entry's `shadowExpire` and the local
account's expiry are that day, both from `people_login_ends` (one derivation,
`people_login_expires` in plugins/filter/people.py), so login stops then without a deploy.
Slurm stops earlier, the day after `ends`, also without a deploy: with
`slurm_enable_submit_holds`, `roles/submit_holds` holds everyone past their end, and
`sbatch`, `srun` and `salloc` refuse their new jobs with the dates (running jobs go on).
The next deploy then deletes their Slurm associations (`slurm_accounting_revoke`, from
`people_former`: left, or past `ends`), whatever `slurm_accounting_prune` says. Their
container image stores age out unused (`container_storage_gc`). Their files stay for
`people_leave_keep_days` (60, `cluster_user_stale_days`) after `ends`; removing them is an administrator's step
(`playbooks/utilities/people-purge.yml`), which the alert `PeopleFilesDue` asks for:
each deploy publishes when every such directory falls due, on the host where it is
(`people_purge_due_timestamp_seconds`), so the day passing needs no deploy.
Setting someone back to `status: active`, or dropping `ends:`, gives the account back: the
entry loses its expiry and nologin shell (a new password is set, since leaving removed
it), and a local account loses the lock `roles/people` put on it (expiry day 1, the `!`
before the hash).

Access rules:

- A password is readable by nobody. A bind checks it, and its owner may change it.
- Everything else is readable by whoever bound: SSSD, Dex, a person.
- Anonymous binds see nothing.
- `olcSecurity: ssf=71` refuses any operation that is not over TLS or the local socket.

Root manages the server over the local socket (`ldapi:///`, SASL EXTERNAL).

The CA (`roles/internal_ca`) lives on the server and lasts 20 years. It signs the
server's certificate for `ldap_server_names` and renews it 30 days before it expires.
Every client trusts the CA at `ldap_ca_file`. The CA is never bought and never renewed
by hand.

A daily timer dumps the configuration and the tree into `/var/backups/slapd` (root only:
the tree holds password hashes).

## Moving from NIS to LDAP

The cluster keeps working at every step, and each step is reversible until NIS goes.
Run each command from `~/deepops`. Preview with `--check` first.

1. **Stand up the directory next to NIS.** In `group_vars/all.yml`, set
   `ldap_suffix`. In the inventory, add the controller to `[ldap-server]`. In
   `group_vars/ldap-server.yml`, set `ldap_server_password_source: shadow`, so every
   run copies each person's hash from the NIS master's `/etc/shadow` and users keep
   their passwords.

   ```sh
   uv run ansible-playbook -K playbooks/generic/authentication.yml -l <controller>
   ```

   On the controller, check that a person's own password binds:

   ```sh
   LDAPTLS_CACERT=/etc/ldap/deepops-ca.crt ldapwhoami -x -ZZ -H ldap://<controller> -D uid=<login>,ou=people,<suffix> -W
   ```

2. **Canary.** Choose one drained node and set `directory_service: ldap` in its
   `host_vars`. Then run:

   ```sh
   uv run ansible-playbook -K playbooks/generic/authentication.yml -l <node>,<controller>
   ```

   On the node, check:

   - `sssctl domain-status cluster` reports Online.
   - `id <admin>` lists the Docker group.
   - `getent netgroup <admin netgroup>` answers.
   - `sudo -v` works as an administrator.
   - An SSH password login works.
   - A job (`srun id`) shows the same groups.

   NIS stays installed on the node, unused. To roll back, delete the host_vars line and
   rerun; the NIS client takes its `nis` source back.

3. **The rest.** Set `directory_service: ldap` in `group_vars/all.yml`, run the same
   playbook for every node, and check as in step 2.

4. **Passwords to the directory.** Set `ldap_server_password_source: ldap`. Then retire
   the controller's local copies of the roster accounts: they shadow the directory, and
   `passwd` on the controller would change the local password.

   ```sh
   uv run ansible-playbook -K playbooks/utilities/retire-local-accounts.yml -l <controller> --check
   uv run ansible-playbook -K playbooks/utilities/retire-local-accounts.yml -l <controller>
   ```

   The playbook keeps a copy of `passwd`, `group`, `shadow` and `gshadow` under
   `/var/backups/deepops-local-accounts`. It removes an entry only where the directory
   answers with the same number. It keeps the break-glass administrators, the members of
   the local `sudo` group. `userdel` also drops a user's subordinate ID ranges
   (`/etc/subuid`, `/etc/subgid`), which the directory does not serve; the playbook
   copies them first and puts them back.

5. **Retire NIS.** Set `nis_domain: ""` and `nis_server: ""`, and add `nis_client` and
   `nis_server` to `deepops_component_removals`. Then run `playbooks/slurm-cluster.yml`;
   its removal phase takes the clients off first, then the master.

## Logging in between hosts

With `sshd_hostbased_group` (roles/sshd, e.g. `slurm-cluster`) a user logged in on one
member reaches the others as themselves by host-based authentication, with no key of
their own; a Slurm node's PAM still admits only users with a job there, and root is
never admitted this way. Each member proves itself with its host key: the role reads
every member's current keys and cluster address (`cluster_networks`) and writes
`/etc/ssh/ssh_known_hosts`, `/etc/ssh/shosts.equiv` and the client setting
(`/etc/ssh/ssh_config.d/20-hostbased.conf`, signing through `ssh-keysign`). It also
retires the old login scripts (`/etc/profile.d/ssh-key.sh`, `.csh`) that made every
user an unprotected key pair and authorized it; keys users already have stay theirs.
Setting the group back to `""` removes the files.

## Running it

- **People join, change or leave in `people.yml`.** One `slurm-cluster.yml --tags people`
  run applies it everywhere the roster reaches: the account, the home, the Slurm
  association, the disk quota, the podman ID range, the chat IDs (every play whose role
  reads the roster carries the tag; `tests/people/test_people_tag.py` keeps it so). The
  change is in the directory then. Every node it ran on forgets its cached answers at once. Other nodes
  follow within `sssd_client_cache_timeout` (ten minutes), or at once after
  `sss_cache -E`.
- **A new person has no password until an administrator sets one** on the server:

  ```sh
  sudo ldappasswd -Y EXTERNAL -H ldapi:/// -S uid=<login>,ou=people,<suffix>
  ```

  After that, they change it with `passwd` on any node.
- **Check a node:** `sssctl domain-status cluster` and `sssctl user-checks <login>`.
- **Restore** into an empty server:
  1. Stop slapd.
  2. `slapadd -n 0 -F /etc/ldap/slapd.d` from the newest `slapd-0-*.ldif.gz`.
  3. `slapadd -n 1` from `slapd-1-*.ldif.gz`.
  4. `chown -R openldap:openldap /etc/ldap/slapd.d /var/lib/ldap`.
  5. Start slapd and rerun the playbook.

  Everything except the passwords comes back from the roster anyway.
- **Removing the directory:**
  1. Name `sssd_client` and `ldap_server` in `deepops_component_removals`.
  2. Set `directory_service` to the directory that replaces it.
  3. Clear `ldap_suffix`.

  The database stays: slapd keeps it at the purge, and moves it under `/var/backups`
  when installed again. The daily dumps stay too.
