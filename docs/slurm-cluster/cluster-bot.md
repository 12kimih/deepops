# Cluster bot (Slack)

`roles/cluster_bot` runs the cluster's Slack app on the first `slurm-master`: slash
commands, forms and buttons for requests users make themselves. It connects to Slack in
Socket Mode, an outbound WebSocket, so no port is opened and no public URL is needed.
It runs as its own account (`cluster_bot_user`, from `system_accounts`) under a hardened
systemd unit, and acts on other hosts only over its SSH key, whose one forced command
there runs only the services the host offers (below).

Features today: account requests, unban requests, collaboration folders (below), and
reminders before a person's last day.

Account requests (`/join`, `/leave`): a newcomer gives a login, a name, a team (the
accounting file's accounts without children, but `cluster_bot_join_exclude`) and an SSH
public key; a person leaving (or an administrator, for anyone) asks to give an account
back. Both are checked first, wait for an administrator's button, and then become a pull
request on the site configuration made with the roster's own editors
(`plugins/filter/people.py`, as `scripts/deepops/people.py add` and `leave`). An
administrator merges it and runs `slurm-cluster.yml --tags people`; the roster stays the
only source of accounts.

Before a person's last day (`ends` in the roster) the bot DMs them
`cluster_bot_leave_remind_days` before (14, 7 and 1 days by default, the same
`cluster_bot_remind_days`): new jobs stop after that day, login stops
`people_leave_grace_days` later, so files are to be moved before then. The dates come from
the roster as deployed (`people_leaving` in `plugins/filter/people.py`, the same
derivation the accounts apply), so a changed `ends` reaches the bot with the next `--tags
people` run and its countdown starts over. A stage the deadline was already inside when
the bot first saw it is skipped (an allocation made for ten days gets no 14-day reminder;
a bot started late does not catch up), except the last, which is always sent.

Collaboration folders (`/project` and the Home tab): start a project, ask to join one,
and, as its owner, add people with a role (owner, member, viewer), change roles, take
people out, change its purpose or ask to close it. Each is a pull request on the roster's
`projects`; joining waits for one of the project's owners (a DM with buttons), starting
and closing for an administrator, the rest opens at once. After a deploy everyone whose
role changed is told. See [shared-space.md](shared-space.md#in-slack-the-cluster-bot).

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
(`cluster_tools.bot_serve`) runs a service only from a file of root's that nobody else may
write, never through a shell.

| Service | Offered by | Command | Requests |
|---|---|---|---|
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

The Home tab (the bot's profile in Slack) shows each person whether job notices reach
them, with a switch to turn them off or on, and their collaboration folders.

Job notices (`roles/job_notice`, switch `slurm_enable_job_notices`, on by default):
Slurm's MailProg on the controllers is `job-notice`, which sends a job's notice as a
Slack DM instead of mail. Turning it on does not notify every job: only a job that asks
gets one, as Slurm defines it. What to tell users:

```bash
#SBATCH --mail-type=END,FAIL          # in a job script: a DM when this job ends or fails
sbatch --mail-type=END,FAIL job.sh    # the same on the command line
export SBATCH_MAIL_TYPE=END,FAIL      # in ~/.bashrc: every job they submit asks
```

Other types are Slurm's (`BEGIN`, `TIME_LIMIT_80`, `ALL`, ...; sbatch(1)); the notice goes to
the job's own user unless `--mail-user` names another. A person's notices within ten minutes of the first become one
message updated in place, so an array of jobs is one DM. The Home tab's switch writes
`cluster_bot_prefs_file`, which `job-notice` reads, so it needs the bot on the
controller (the first `slurm-master`, its default host). Without `slack_bot_token`
nothing is sent and jobs are unaffected. A site's own slurm.conf template must say
`MailProg={{ job_notice_command }}`; the deploy stops on any other while job notices are
on, since Slurm would send them as local mail instead.

## Turning it on

1. Create the Slack app from the manifest (below; api.slack.com/apps, "From a manifest"),
   install it to the workspace, and create an app-level token with `connections:write`.
2. Vault both tokens in the site config: `slack_bot_token` (xoxb-, already used by the
   notifiers) and `slack_app_token` (xapp-).
3. Set `slurm_enable_cluster_bot: true`.
4. Run `playbooks/generic/cluster-bot.yml` (it also authorizes its key where fail2ban
   offers the bans service); preview it with `--check --diff` first.

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
| `/var/lib/cluster-bot/` | Its SSH key and state: held requests, reminders sent, bans seen (kept on removal) |
| `/etc/cluster-bot-access/<service>.json` | On a host it acts on: a service offered to it |
| `/var/lib/cluster-bot/.ssh/authorized_keys` | On a host it acts on: its key, for the dispatcher only |
