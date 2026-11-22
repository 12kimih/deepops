# The shared space

`roles/shared_space` (switch `slurm_enable_shared_space`, off by default) lays out the
shared part of `/home` on the home server, the first `slurm-nfs` host, where `/home` is
local. The settings are `roles/shared_space_defaults`' (the role, the directory roles and
the cluster bot read them):

| Path | What it holds | Who writes | Whose space |
|---|---|---|---|
| `/home/shared/datasets`, `models`, `containers` (`shared_space_data`) | Data many people read: public datasets, model weights, container images | The account `shared` (`shared_space_writer`), whose home `/home/shared` is | Its own budget (a project quota on `/home`, [disk-quota.md](../deepops/disk-quota.md#project-quotas)) |
| `/home/shared/projects/<name>` | A collaboration folder | Its owners and members | Each file counts to its writer's quota |

`/home/shared` is the account's home as `/home/<login>` is anyone's (`shared_space_root`
reads it from `system_accounts`); it stays root's, so the account can rename nothing at
its top. No person may be called `shared`: a login that is a service account's name is
refused (roles/people), and the roster's `reserved_logins` keeps the bot from proposing
one.

## Shared data

Read-only to everyone, writable by `shared` alone (mode 2755). People ask for what they
need in the help channel; what is downloaded there is stored once for everyone instead
of in each home. Read models by their path; keep `HF_HOME` (tokens) in your own home.

## Collaboration folders

A project has three roles, as GitHub, GitLab and Google Groups have:

| Role | Files | May also |
|---|---|---|
| owners (one or more) | read and write | add and remove people, change roles, approve join requests, change the purpose, ask to close it |
| members | read and write | leave |
| viewers | read | leave |

Each project is two cluster groups, published by the directory with the others
(`directory_groups_all`, NIS or LDAP), so they mean the same on every node and inside
jobs: `<name>` (owners and members) and `<name>-read` (viewers). The folder is
`root:<name>`, mode 2770, with ACLs: whatever anyone adds stays the group's to change
whatever their umask, and `<name>-read` reads everything. A role change is a group
membership change and never touches a file, however large the folder. There is always
an owner: the last one can neither leave nor step down.

The projects live in the people roster, `projects` (`config.example/people.yml`),
checked with it (active people only, one role each, GIDs no person's UID), and reach the
roles as `shared_projects: "{{ people_roster.projects | default({}) }}"`.

### In Slack (the cluster bot)

| Who | What | How | Decided by |
|---|---|---|---|
| anyone | start a project (they own it), with other owners, members and viewers | `/project`, or the Home tab's "새 프로젝트" | an administrator |
| anyone | join a project as a member or a viewer | Home tab, "참여 요청" | any one of its owners (a DM with buttons) |
| an owner | add someone, change their role, take them out | Home tab, the project's menu | at once |
| an owner | change the purpose | the project's menu | at once |
| an owner | close the project | the project's menu | an administrator |
| anyone in it | leave | the project's menu | at once |

Each request becomes a pull request on the roster (`cluster_bot.projects`); "at once" means
the pull request opens without waiting for anyone, and an administrator reviews it as they
merge. Once it is merged and deployed (`slurm-cluster.yml --tags people`: the groups, then
the folder), everyone whose role changed gets a DM saying so and that a new login applies
it. The Home tab lists each person's projects with their role, the path and the people.
A new project's GIDs are the lowest free from `shared_projects_gid_min` (50000), past the
site's other cluster groups.

A project closed keeps its folder and files (its groups go); every run of the role lists
such folders, for an administrator to move to an owner's home. Nothing here is ever
removed by a deploy, nor when the switch is turned off.

## Turning it on

1. `slurm_enable_shared_space: true`, and `shared_projects` as above.
2. The groups first: `playbooks/generic/authentication.yml` (or `--tags people`), so the
   project groups resolve on the home server.
3. `playbooks/slurm-cluster/shared-space.yml -l <home server> --check --diff`, then
   without `--check`.
4. For Slack requests: the cluster bot with its GitHub App (cluster-bot.md), and
   `/project` in its app manifest.
5. For the shared data's budget, a project quota on its directories once `/home` has the
   project feature (disk-quota.md: adding the quota features to /home).
