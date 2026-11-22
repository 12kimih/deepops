# The shared space

`roles/shared_space` (switch `slurm_enable_shared_space`, off by default) lays out the
shared part of `/home` on the home server, the first `slurm-nfs` host, where `/home` is
local:

| Path | What it holds | Who writes | Whose space |
|---|---|---|---|
| `/home/shared/datasets`, `models`, `containers` (`shared_space_data`) | Data many people read: public datasets, model weights, container images | One account, `shared_space_writer` (`shareddata`, roles/system_accounts) | Its own budget (a project quota on `/home`, [disk-quota.md](../deepops/disk-quota.md#project-quotas)) |
| `/home/shared/projects/<name>` | A collaboration folder | Its members (a group of their own) | Each file counts to its writer's quota |

The root is `shared_space_root` (`/home/shared` from `people_home_root`). Its name is no
one's login: list it in the roster's `reserved_logins` (`config.example/people.yml`), and
the roster check refuses a person by that name.

## Shared data

Read-only to everyone, writable by the writer account alone (mode 2755). People ask for
what they need (the help channel); what is downloaded there is stored once for everyone
instead of in each home. Read models by their path; keep `HF_HOME` (tokens) in your own
home.

## Collaboration folders

Declared once, in `shared_projects` (roles/directory_defaults):

```yaml
shared_projects:
  vision-benchmark: {gid: 5101, members: [alice, bob], lead: alice, purpose: shared eval set}
```

Each project is a group of the same name, with that GID and those members, published
by the directory with the other cluster groups (`directory_groups_all`: NIS or LDAP), so
it means the same on every node and inside jobs. Its folder is `root:<group>`, mode
2770 (members only; new files keep the group) with default ACLs that make whatever a
member adds the group's to change too, whatever their umask. The lead asks for member
changes; a member removed from the list loses access on the next run.

A project taken off the list keeps its folder and files (its group goes); every run
lists such folders, for an administrator to move to the lead's home. Nothing here is
ever removed by a deploy, nor when the switch is turned off.

## Turning it on

1. The groups first: run the directory's playbook (`playbooks/generic/authentication.yml`)
   so the project groups resolve on the home server.
2. `playbooks/slurm-cluster/shared-space.yml -l <home server> --check --diff`, then without
   `--check`.
3. For the shared data's budget, a project quota on its directories once `/home` has the
   project feature (disk-quota.md: adding the quota features to /home).
