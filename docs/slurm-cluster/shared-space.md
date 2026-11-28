# The shared space

`roles/shared_space` (switch `slurm_enable_shared_space`, off by default) lays out the
shared part of `/home` on the home server, the first `slurm-nfs` host, where `/home` is
local. The settings are `roles/shared_space_defaults`' (the role, the directory roles and
the cluster bot read them):

| Path | What it holds | Who writes | Whose space |
|---|---|---|---|
| `/home/shared/datasets`, `models`, `containers` (`shared_space_data`) | Data many people read: public datasets, model weights, container images | The account `shared` (`shared_space_writer`), whose home `/home/shared` is | One budget, `shared_space_data_budget` (20T), below |
| `/home/shared/projects/<name>` | A collaboration folder (`place: home`, the default) | Its owners and members | Each file counts to its writer's quota |
| `<scratch_space_root>/projects/<name>` | A collaboration folder on the scratch space (`place: scratch`, made by `roles/scratch_space` on the scratch server with the same groups and ACLs, `folders.yml`) | Its owners and members | Each file counts to its writer's scratch quota; files unused past the stale period go as everywhere on the scratch space |

A project's `place` in the roster (`home` or `scratch`) says where its folder lives;
the cluster bot's new-project form asks for it.

`/home/shared` is the account's home as `/home/<login>` is anyone's (`shared_space_root`
reads it from `system_accounts`); it stays root's, so the account can rename nothing at
its top. No person may be called `shared`: a login that is a service account's name is
refused (roles/people), and the roster's `reserved_logins` keeps the bot from proposing
one.

## Shared data

Read-only to everyone, writable by `shared` alone (mode 2755). Read models by their
path; keep `HF_HOME` (tokens) in your own home.

**Asking for it.** `/cluster data` in Slack opens a form: the kind (datasets, models,
containers), a name (the folder), the public https URL, the size and what it is for, and
the sha256 when the source gives one. The bot refuses an existing name or a size the
budget cannot take (counting downloads still running); otherwise an administrator
approves it in the administrators' channel. Then the home server's
`cluster-shared-data` (`cluster_tools.shared_data`, the bot's service there through
`roles/cluster_bot_access`) checks it again and starts the download as a transient
systemd unit running as `shared`, at the lowest best-effort I/O priority, into
`/home/shared/<kind>/<name>/`. The requester gets a DM when it is there or failed.

- Public sources only: `https`, no credentials in the URL, a host that resolves to
  global addresses alone (never a private, loopback or link-local network), every
  redirect checked the same way, and the connection made to the address checked (TLS
  still verifies the name; no proxy from the environment); `shared_space_data_sources` limits the hosts (each and
  below it) when set.
- The file is kept as published: an archive stays packed (many small files on the
  shared filesystem slow everyone; jobs unpack into their own `/tmp`). Its sha256 is
  checked when one was given, and it may not grow past the size asked (plus 10%).
- The ledger, `/var/lib/shared-data/ledger.json`, records each entry: who asked, from
  where, the size, the checksums, when it came and the uses people recorded.
- Never on `/scratch`: the shared data is kept, the scratch space is cleaned.

**The budget.** `shared_space_data_budget` (20T) covers all the data directories
together. Until `/home` has project quotas, `shared-data-metrics.timer` (hourly) exports
the usage as the disk quota metrics' project series (`project="shared-data"`, hard = the
budget, warn = `cluster_user_warn_fraction` of it), so the daily disk report lists it,
puts it on the card once past the warn level, and `DiskQuotaProjectOverWarnLevel`
fires. Once `/home` has the project feature (disk-quota.md), give the data directories
their project with `block_hard: "{{ shared_space_data_budget }}"` in
`disk_quota_projects` and set `shared_space_data_quota: true`: the kernel then enforces
the same figure and disk_quota exports it instead.

**Cleanup.** The cluster's one rule for unused files, as on `/scratch`: an entry nobody
used for `shared_space_data_stale_days` (`cluster_user_stale_days`, 60) gets a notice,
a DM to its requester; used again within `shared_space_data_grace_days`
(`cluster_user_grace_days`, 14) the notice is over, otherwise the bot removes the entry
and tells the requester and the administrators. Use is read from the entry's files, the
later of their access and modification time (`cluster_tools.cleanup`), so nobody records
anything. That needs the filesystem to record reads: `/home` mounted `relatime` (with
`lazytime`, so the access times cost the disks next to nothing), never `noatime`
(`host_baseline_mount_options`). A node's read over NFS reaches the server's access time;
one served from that node's cache does not, which over 60 days leaves at most an entry
read only from one node's memory looking unused. The notice and the grace period cover
it, and nothing is lost for good: the ledger keeps the source and the checksum, so a new
request brings the same file back. A removal that fails is told to the administrators
once per reason; the rounds keep retrying.

**Alerts** (`roles/prometheus` rules `shared-space.yml`): `SharedDataUsageStale` and
`SharedDataUsageNeverReported` when the hourly export stops (the report's figures go
old); `SharedDataCleanupStale` when the bot has not read the ledger for 3 hours (its
rounds stopped: no download is told, no notice or removal happens). The service stamps
each read, exported only while the bot's service is offered.

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
| anyone | start a project (they own it), with other owners, members and viewers | `/cluster project`, or the Home tab's "새 공동작업 폴더" | an administrator |
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
4. For Slack requests: the cluster bot with its GitHub App (cluster-bot.md); its one
   command's `project` subcommand comes with the generated manifest.
5. For shared data requests, the cluster bot (it reaches the home server's
   `shared-data` service; run `playbooks/generic/cluster-bot.yml` after this playbook
   so its key is authorized there). For the budget as a kernel limit, the project
   quota above once `/home` has the project feature.
