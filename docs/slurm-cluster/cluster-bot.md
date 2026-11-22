# Cluster bot (Slack)

`roles/cluster_bot` runs the cluster's Slack app on the first `slurm-master`: slash
commands, forms and buttons for requests users make themselves. It connects to Slack in
Socket Mode, an outbound WebSocket, so no port is opened and no public URL is needed.
It runs as its own account (`cluster_bot_user`, from `system_accounts`) under a hardened
systemd unit, and acts on other hosts only over its SSH key, whose one forced command
there runs only the services the host offers (below).

Features today: scratch allocations (`/scratch`, see [scratch-alloc.md](scratch-alloc.md)):
ask, extend, resize and release from a form; requests within the automatic limits are
applied at once, larger ones wait for an administrator's button in the admin channel;
owners get a DM `cluster_bot_scratch_remind_days` before an allocation ends, with an extend button.

Account requests (`/join`, `/leave`): a newcomer gives a login, a name, a team (the
accounting file's accounts without children, but `cluster_bot_join_exclude`) and an SSH
public key; a person leaving (or an administrator, for anyone) asks to give an account
back. Both are checked first, wait for an administrator's button, and then become a pull
request on the site configuration made with the roster's own editors
(`plugins/filter/people.py`, as `scripts/deepops/people.py add` and `leave`). An
administrator merges it and runs `slurm-cluster.yml --tags people`; the roster stays the
only source of accounts.

Before a person's last day (`ends` in the roster) the bot DMs them
`cluster_bot_leave_remind_days` before (30, 14, 7 and 1 days by default): new jobs stop
after that day, login stops `people_leave_grace_days` later, so files are to be moved
before then. The dates come from the roster as deployed (`people_leaving` in
`plugins/filter/people.py`, the same derivation the accounts apply), so a changed `ends`
reaches the bot with the next `--tags people` run and its countdown starts over.

Unban requests (`/unban-request`): someone whose address fail2ban banned (bans are for
good, `roles/fail2ban`) gives the address and what happened. The bot checks the address
is banned now on a host where fail2ban runs, an administrator approves, and the ban is
lifted on every such host (`fail2ban-client unban`, which also drops it from fail2ban's
database). The form refuses what is not an IP address before anything is asked.

The administrators see the bans in their channel without asking: every five minutes the
bot reads each such host's list, tells the bans made since its last look in one message
(a wave of bans is one message), and keeps one pinned message listing the bans now,
newest first, edited in place. Its first look only learns the bans made before it; a
host it cannot read keeps its bans on the list as last seen and is named there. No Slack
token is ever on those hosts: the bot reads them over the `bans` service. Invite the bot
to the admin channel so it can pin the list.

### How the bot acts on other hosts

A role that lets the bot do something on its hosts offers a service through
`roles/cluster_bot_access`: a file `/etc/cluster-bot-access/<service>.json` naming the
command, which runs as root with the rest of the request and takes only what the bot may
ask. The bot's key is authorized there for one forced command,
`sudo -n /usr/local/sbin/cluster-bot-serve /etc/cluster-bot-access`, the only command its
sudo rule allows, and only from the bot host's cluster address. The dispatcher
(`clustertools.bot_serve`) runs a service only from a file of root's that nobody else may
write, never through a shell.

| Service | Offered by | Command | Requests |
|---|---|---|---|
| `scratch` | `roles/scratch_alloc` | `scratch-alloc ... serve` | create, resize, extend, release, list |
| `bans` | `roles/fail2ban` (while there is a bot) | `cluster-bans serve` | list, unban IP |

The shared pieces (dispatcher, sudo rule, authorized key) are items of every role that
offers a service, so `roles/component_record` removes them with the last of those roles.
`playbooks/generic/cluster-bot.yml` authorizes the bot's key on the hosts that already
offer a service right after making it, so a first deploy needs no second run.

### The GitHub App for account requests

1. On GitHub, under the organization's settings, create a GitHub App: no webhook;
   repository permissions Contents (read and write) and Pull requests (read and write),
   nothing else.
2. Install it on the site configuration repository only. Note the App ID and the
   installation ID (the number at the end of the installation's settings URL).
3. Generate a private key (PEM) and vault it in the site config as
   `cluster_bot_github_app_key`; set `cluster_bot_github_repo` (owner/name),
   `cluster_bot_github_app_id` and `cluster_bot_github_installation_id`.
4. Protect the base branch (`cluster_bot_github_base`) so a pull request needs a review:
   the App can open pull requests, never merge them.

The bot signs the App's token with the system's `openssl`; tokens last an hour.

The Home tab (the bot's profile in Slack) shows each person their scratch allocations and
whether job notices reach them, with a switch to turn them off or on.

Job notices (`roles/job_notice`, switch `slurm_enable_job_notices`, on by default):
Slurm's MailProg on the controllers is `job-notice`, which sends a job's notice as a
Slack DM instead of mail. Only jobs that ask get one, as Slurm defines it:
`sbatch --mail-type=END,FAIL`, or `export SBATCH_MAIL_TYPE=END,FAIL` in a person's
shell for every job. A person's notices within ten minutes of the first become one
message updated in place, so an array of jobs is one DM. The Home tab's switch writes
`cluster_bot_prefs_file`, which `job-notice` reads, so it needs the bot on the
controller (the first `slurm-master`, its default host). Without `slack_bot_token`
nothing is sent and jobs are unaffected.

## Turning it on

1. Create the Slack app from the manifest (below; api.slack.com/apps, "From a manifest"),
   install it to the workspace, and create an app-level token with `connections:write`.
2. Vault both tokens in the site config: `slack_bot_token` (xoxb-, already used by the
   notifiers) and `slack_app_token` (xapp-).
3. Set `slurm_enable_cluster_bot: true` (and, for scratch, `slurm_enable_scratch_alloc:
   true` with the scratch server in `[scratch-server]`).
4. Run `playbooks/generic/cluster-bot.yml` (it also authorizes its key where fail2ban
   offers the bans service), then `playbooks/generic/scratch-alloc.yml`; preview each with
   `--check --diff` first.

Without both tokens the bot is installed but stopped. Who the bot knows comes from the
people roster: `cluster_bot_members` (each person's `slack` attribute) and
`cluster_bot_admins` (people with the `admin` role).

## Slack app manifest

[`roles/cluster_bot/files/slack-app-manifest.yml`](../../roles/cluster_bot/files/slack-app-manifest.yml)
is the app's manifest: its slash commands, scopes, interactivity and Socket Mode. Paste
it when creating the app; when a feature adds a command, the manifest changes with it and
the app's settings are updated from it.

## Files

| Path | What |
|---|---|
| `/etc/cluster-bot/config.json` | Channels, members, admins, feature settings (0640, the bot's group) |
| `/etc/cluster-bot/bot.token`, `app.token` | The tokens, read by the bot alone; never on a command line |
| `/etc/cluster-bot/github-app.pem` | The GitHub App's private key, read by the bot alone |
| `/etc/cluster-bot/known_hosts` | The host keys of the hosts it reaches, read from each host |
| `/var/lib/clusterbot/` | Its SSH key and state: held requests, reminders sent, bans seen (kept on removal) |
| `/etc/cluster-bot-access/<service>.json` | On a host it acts on: a service offered to it |
| `/var/lib/clusterbot/.ssh/authorized_keys` | On a host it acts on: its key, for the dispatcher only |
