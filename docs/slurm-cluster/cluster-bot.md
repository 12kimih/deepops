# Cluster bot (Slack)

`roles/cluster_bot` runs the cluster's Slack app on the first `slurm-master`: slash
commands, forms and buttons for requests users make themselves. It connects to Slack in
Socket Mode, an outbound WebSocket, so no port is opened and no public URL is needed.
It runs as its own account (`cluster_bot_user`, from `system_accounts`) under a hardened
systemd unit, and acts on other hosts only over its SSH key, whose one forced command
there runs only the services the host offers (below).

Every request is a subcommand of ONE slash command, `cluster_bot_command` (`/cluster`
by default; just `/cluster` is the help):

<!-- BEGIN GENERATED: bot-subcommands -->
- `/cluster join`: ask for a cluster account
- `/cluster leave`: give back your cluster account
- `/cluster unban`: ask to lift a ban after failed logins
- `/cluster project`: start a collaboration folder
- `/cluster data`: ask for public data stored once for everyone
- `/cluster usage`: where you stand against every rule, as the usage command shows it
- `/cluster help`: this list
<!-- END GENERATED: bot-subcommands -->

Slack's slash commands are not namespaced (the app installed last wins a name) and
`/leave` is Slack's own, so the bot claims one name; a second cluster in the same
workspace sets another. The subcommands are one table (`cluster_bot/commands.py`,
`SUBCOMMANDS`) that the dispatcher, the help, the Home tab, the Slack app manifest and
the list above all come from (`scripts/docs/generate.py`); a test requires a handler for
each, and no command name Slack uses itself. App commands do not run inside message
threads: use them in a channel or the bot's DM.

Features: account requests, unban requests, collaboration folders and shared data
requests (below), reminders before a person's last day, the pinned ban list, the docs
site's notices posted on their date and each channel's guide.

Account requests (`/cluster join`, `/cluster leave`): a newcomer gives a login, a name, a team (the
accounting file's accounts without children, but `cluster_bot_join_exclude`) and an SSH
public key; a person leaving (or an administrator, for anyone) asks to give an account
back. Both are checked first, wait for an administrator's button, and then become a pull
request on the site configuration made with the roster's own editors
(`plugins/filter/people.py`, as `scripts/deepops/people.py add` and `leave`). An
administrator merges it and runs `slurm-cluster.yml --tags people`; the roster stays the
only source of accounts.

Every DM the bot sends goes through `cluster_tools.slack` (the cluster's tools library,
which the bot imports): the notices (the decision on a request, a request held for a
project's owner, reminders, the account notice, a project's change, the shared data's
cleanup) are logged for the administrators in `slack_dm_log_channel`, one daily thread,
as the tools' DMs are; the answer to what a person just did (a join reply, "recorded")
is not. Each reply holds the DM as the person got it, under a line naming its kind, the
login and a link; the day's message and its count are read from Slack, so the bot and
the usage reports share them. Every sender counts its DMs as sent, failed or
log_failed in its textfile in `node_exporter_unprivileged_dir`
(`slack_dm_messages_total`), which `SlackDmFailing` and `SlackDmLogFailing` read; the
usage report also stamps its hourly run (`SlackDmSenderStale`).

Every channel post goes through `cluster_tools.slack` too, and a top-level one says what
it is (`CLASSES`: a message kept up to date, a day log's message, a scheduled report, an
administrators' action, the answer to a request) or is refused; a reply is bounded by its
parent. A person is told only by DM: no channel post mentions one (the help channel's
lines on a request name the person as text), and only an administrators' action (a held
request, missing scopes, a shared entry that could not be removed, and the usage
report's weekly idlers and mass access-time refreshes) mentions their group, each posted
once per its key within its repeat, read from the channel (owner decision 12,
2026-11-28). Each post counts in `slack_posts_total` by channel role, class and place.
Past 20 top-level posts of the bot in one channel within the hour (`POST_BUDGET`, read
from Slack, every sender together), the rest of the hour go in the thread of one message
saying so, and `SlackChannelPostsHigh` names the channel; what the administrators need
not act on (new bans, deletes, removed shared entries, the DMs) goes in day logs, one
message a day each, mentioning no one.

What a newcomer needs before the first login (`cluster_bot_connect`: the SSH host and
port and the guide from `cluster_info`, the portal's `open_ondemand_url`
(roles/open_ondemand_defaults) when Open OnDemand is installed) is in the `/cluster join` reply,
with how an account comes about, and in a DM the bot sends once the person's account
exists on its host (checked every `REMIND_EVERY` seconds, `cluster_bot/app.py`; the first run only records the accounts there are,
in `accounts-welcomed.json`). Slack only: these facts never go to the public docs.

Before a person's last day (`ends` in the roster) the bot DMs them
`cluster_bot_leave_remind_days` before (14, 7 and 1 days by default, the same
`cluster_bot_remind_days`): new jobs stop after that day, login stops
`people_leave_grace_days` later, so files are to be moved before then. The dates come from
the roster as deployed (`people_leaving` in `plugins/filter/people.py`, the same
derivation the accounts apply), so a changed `ends` reaches the bot with the next `--tags
people` run and its countdown starts over. A stage the deadline was already inside when
the bot first saw it is skipped (an allocation made for ten days gets no 14-day reminder;
a bot started late does not catch up), except the last, which is always sent.

Collaboration folders (`/cluster project` and the Home tab): start a project, ask to join one,
and, as its owner, add people with a role (owner, member, viewer), change roles, take
people out, change its purpose or ask to close it. Each is a pull request on the roster's
`projects`; joining waits for one of the project's owners (a DM with buttons), starting
and closing for an administrator, the rest opens at once. After a deploy everyone whose
role changed is told. See [shared-space.md](shared-space.md#in-slack-the-cluster-bot).

Shared data requests (`/cluster data`): public data stored once for everyone under
`/home/shared/<kind>/<name>`, approved by an administrator, downloaded by the home
server's `shared-data` service as the shared account; the bot tells the requester when
it is there, and runs its cleanup notices (shared-space.md#shared-data).

Unban requests (`/cluster unban`): someone whose address fail2ban banned (bans are for
good, `roles/fail2ban`) gives the address and what happened. The bot checks the address
is banned now on a host where fail2ban runs, an administrator approves, and the ban is
lifted on every such host (`fail2ban-client unban`, which also drops it from fail2ban's
database). The form refuses what is not an IP address before anything is asked.

The administrators see the bans in their channel without asking: every five minutes the
bot reads each such host's list, records the bans made since its last look in the day's
ban log (one message a day, each round's new bans in its thread, mentioning no one: a
ban needs no one to act, and a person locked out asks with `/cluster unban`), and keeps
one pinned message of the bans now: the count, each host's count and the newest ban, with every ban,
newest first, in its thread, as many replies as Slack's limits need, edited in place.
Nothing is cut. Its first look only learns the bans made before it; a host it cannot
read keeps its bans on the list as last seen and is named there.

Every message the bot keeps up to date (the ban list, the DM log's day message) carries
Slack's message metadata and is found in the channel's history, not only through the ts
in its state file (`cluster_tools.slack.keep`): a lost or older state never posts a
second copy, and copies already there are deleted with their replies, the recorded one
(else the newest) kept. The channel guides' bookmarks are found the same way, by the
app that added them, and the docs notices by their own metadata (above), the newest
pinned and any other pin of the bot's taken off. No Slack
token is ever on those hosts: the bot reads them over the `bans` service. Invite the bot
to the admin channel so it can pin the list.

The user docs' notices: when `cluster_info.docs_url` is set, the bot reads
`<docs_url>/notices.json` every `NOTICES_EVERY` seconds (`cluster_bot/app.py`) and posts
each notice in `slack_channels.announce` once, on its date. A notice comes as Markdown
(its title, lead, link and small print) and the bot renders it with
`cluster_tools.rich`, the one renderer of every Slack text; the docs keep no converter
of their own and check each commit with this repository's `notice-render` hook
(`scripts/deepops/notice_render.py`), so a notice the bot could not show fails where it
is written. A notice still published as Slack blocks is posted as it is. The docs repository holds no Slack
credential; the announcement goes through this app.

The bot keeps the announcement channel like `notices.json` (`cluster_bot.notices`):
each notice due has one post, showing it as today's code renders it. A notice reworded
on the site, or rendered differently by a newer `rich`, edits its post in place
(`chat.update`), once. A notice the site dropped has its post deleted, with the bot's own
replies under it (a person's reply stays, under Slack's "deleted" mark), and so does a
second post of one notice. The newest post is the one pinned. Each post carries Slack
message metadata (`event_type` `cluster_notice`, the notice's id), so the bot finds its
posts exactly. Its first look posts nothing: every notice already due counts as
announced, so a new deploy never repeats one. A notice dated later still goes out on its
date, and one whose post someone deleted is not posted again. An export with no notice
changes nothing. A post of another app or a person is never touched.

A post from before the metadata (an older bot's, or this app's webhook's) is found by
its button to the notice's page, or by its title. The bot's rounds leave those as they
are; you change them with one run, after reading what it would do:

    sudo cluster-bot notices --dry-run
    sudo cluster-bot notices

`cluster-bot` (`/usr/local/sbin`, the administrators' command) runs the bot's code as the
bot's user with the interpreter, path and config of its unit; the role renders both from
the same vars. The dry run prints each `update`, `delete` (with the replies it takes), `post`, `pin`,
`missing` and `foreign` (another app's post, left) and changes nothing, the record
neither. The second command does it; from then on those posts carry
metadata. A post the app may not change (`cant_update_message`) is said and left.

Messages no round of today's code manages (an alert the app's old incoming webhook
posted and never edited when it resolved, a ban notice of the old format whose list was
cut, a notice of a retired integration) stay until you tidy the channels
(`cluster_bot.tidy`), after reading what it would do:

    sudo cluster-bot tidy --dry-run
    sudo cluster-bot tidy

The dry run surveys each channel `slack_channels` names first: its messages grouped by
who posted them (`bot_id`, `app_id`, user, name, subtype), how many, from when to when,
and whose they are, an integration you name in `cluster_bot_known_integrations` (bot_id
to what it is) by that name. Then it lists what becomes of each message a rule matches
(`RULES` in `cluster_bot.tidy`: the channel's role, what the message is, how it is
recognized), decided before anything is tried and the same in both runs. Slack's
`chat.delete` acts on the token's bot's own messages alone, so only the bot's go, its
replies after them; someone else's reply stays. An integration's message (the app's old
incoming webhook, another app) is listed once per channel as "by hand", with a link to
each, for an admin to delete in the Slack client; tidy never tries it. A person's
message is left. A notice an integration posted is recorded as announced in the notices
round's record (the real run only), so the round never posts or pins it again once it is
deleted by hand.

Slack lets a member delete only their own messages, and an app's only a workspace owner
or admin. So the bot has a message shortcut, "클러스터 봇 글 지우기" (a message's "..." menu,
`cluster_bot.remove`): a member of the administrators' user group (`slack_admin_group_id`,
read from Slack each time) deletes one of the bot's own messages with it, with the bot's
replies under it, and the delete is logged in `slack_channels.admin` like the DMs: one
message a day, each delete in its thread with who (as text, no mention), where, and the
message as it was. It refuses what
Slack would not let the bot delete (an integration's or a person's message, naming who
posted it) and what a feature keeps up to date (the ban list, the DM log's day message),
which would come back. A notice post may go: the notices round keeps it as announced.
The shortcut is in the app manifest the deploy writes; import it once (and reinstall the
app) for the shortcut and its `usergroups:read` scope.

The channels' guides: each channel `slack_channels` names gets its purpose, topic, a
canvas (the channel's README tab) and bookmarks from `<docs_url>/channels.json` (the docs
site's build writes it, keyed by the channel's purpose, the keys of `slack_channels`),
read every `GUIDES_EVERY` seconds (`cluster_bot/app.py`). The purpose and topic are set
only when they differ. The canvas
is created once and replaced in full when its text changes and once a day, so a hand
edit lasts a day at most; it is kept read-only for the channel's members (a refusal is
logged in the bot's journal). Only the bookmarks the bot added are kept in step; one a
person added stays. Invite the bot to every channel it guides: setting a topic needs a
member, and a private channel's canvas too. The public site holds no service address: a
guide writes `[[url:portal]]` or `[[url:dashboard]]`, and the bot fills it from
`cluster_bot_connect` (`portal_url`, `dashboard_url`: `grafana_root_url`, the same address
the GPU usage report links). A line or bookmark whose address the bot has not is left out.

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
| `shared-data` | `roles/shared_space` (while there is a bot) | `shared-data <config> serve` | the shared data requests' downloads and cleanup |

The shared pieces (dispatcher, sudo rule, authorized key) are items of every role that
offers a service, so `roles/component_record` removes them with the last of those roles.
`playbooks/generic/cluster-bot.yml` authorizes the bot's key on the hosts that already
offer a service right after making it, so a first deploy needs no second run.

A role offers its service only while there is a bot, so a host deployed before the bot
was switched on offers nothing yet. The playbook ends by proving each service the bot
uses (`_cluster_bot_services` in `roles/cluster_bot/vars`: the service, its hosts, the
playbook that offers it) as the bot itself: each host is asked over the bot's key which
services it offers (the dispatcher's one request of its own, `offers`, runs nothing). A
host that keeps the key out or lacks the service fails the run with the playbook to run
there, for example
`bans on gpu[1-2] (...): run playbooks/generic/fail2ban.yml -l gpu1,gpu2`, which offers
the service and authorizes the key.

The bot's ssh reads no ssh_config (`-F none`): only its own key, public-key
authentication and the host keys the deploy pinned. A site's client settings (host-based
authentication, shared connections) are for people and Ansible. A failure reaches Slack
as one short reason per group of hosts, with what an administrator does about it.

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

The Home tab (the bot's profile in Slack) shows each person a button for each service
`cluster_bot_connect` gives an address (the web portal, Grafana, the user docs), then one
part per header: where they stand against each rule (the rows `usage` prints, each led
by its state's mark), whether job notices reach them (with a switch), the commands they
have (the bot's and the login nodes', one list each), how to ask for help and their
collaboration folders. Every text goes through `cluster_tools.rich`; the commands are
never small print (a context block holds only a note).

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
the job's own user only: `--mail-user` may name anyone, so it is never a recipient. A person's notices within ten minutes of the first become one
message updated in place, so an array of jobs is one DM. The Home tab's switch writes
`cluster_bot_prefs_file`, which `job-notice` reads, so it needs the bot on the
controller (the first `slurm-master`, its default host). Without `slack_bot_token`
nothing is sent and jobs are unaffected. A site's own slurm.conf template must say
`MailProg={{ job_notice_command }}`; the deploy stops on any other while job notices are
on, since Slurm would send them as local mail instead.

## Turning it on

The cluster has ONE Slack app, whose bot token (`slack_bot_token`) the usage reports (with the disk,
I/O and scratch cleanup notices), the job notices, Alertmanager (`alertmanager_slack_bot`) and
this bot all use. Each deploy writes its manifest,
`/etc/cluster-bot/slack-app-manifest.json`, from the subcommand table: the command, the
app's and bot user's name (`cluster_bot_app_name`, cluster-bot; importing the manifest
into an existing app renames it and keeps its tokens and webhook), interactivity, Socket Mode and every bot scope the cluster's Slack features
need (`cluster_bot.commands.SCOPES`, each with why; a test fails when the code calls a
Slack method whose scope is missing). The same file without a deploy:
`cd roles/cluster_bot/files && python3 -m cluster_bot.commands manifest /cluster cluster-bot [EXTRA SCOPE ...]`
(`cluster_bot_extra_scopes`: scopes the app keeps for other tools, incoming-webhook when
Alertmanager posts through the app's webhook).

The manifest changes the app only when it is imported, so the bot checks what Slack
granted: `cluster-bot-scopes.service` (`cluster_bot.scopes`, as root, daily, after each
boot and at the end of every deploy of the bot) reads the token's scopes and compares
them with `SCOPES`. Each one missing is a series (`cluster_bot_scope_missing{scope}`, the
ClusterBotScopeMissing alert), and the administrators' channel is told once when the
missing set changes, with what to import; ClusterBotScopesUnchecked fires when Slack has
not answered for two days (the check failing or not running).

1. In the app's settings (api.slack.com/apps, the app, "App Manifest"), paste the
   manifest and save; reinstall the app to the workspace when Slack asks (new scopes).
   The first time, the bot is still off and no deploy has written the file: generate it
   with the command above. Reinstalling keeps the bot token; Slack's `auth.test` with
   it answers `ok` and lists the granted scopes in its `x-oauth-scopes` header.
2. Under "Basic Information", "App-Level Tokens", generate a token with
   `connections:write` (xapp-, for Socket Mode).
3. Vault it in the site config as `slack_app_token`, the way `slack_bot_token` is vaulted:
   `cd ~/deepops && uv run ansible-vault encrypt_string --stdin-name slack_app_token`
   (paste the token, then Ctrl-D) and put the result in the group_vars file that holds
   `slack_bot_token`.
4. For account requests, the GitHub App (below): `cluster_bot_github_repo`,
   `cluster_bot_github_app_id`, `cluster_bot_github_installation_id`, and its private key
   vaulted as `cluster_bot_github_app_key`
   (`uv run ansible-vault encrypt_string --stdin-name cluster_bot_github_app_key < key.pem`).
5. Name the administrators' user group (roles/slack_defaults): `slack_admin_group`, the
   @handle people tag in the help channel. Each deploy then asks Slack for its ID
   (`usergroups.list`, the manifest's usergroups:read), uses it for the run and says it;
   pin it as `slack_admin_group_id` to skip the lookup (a set ID always wins). Slack
   notifies a group only by its ID; none found, the notices mention no one.
6. Set `slurm_enable_cluster_bot: true` and run `playbooks/generic/cluster-bot.yml` (it
   also authorizes its key on every host that offers a service: fail2ban's bans, the shared
   space's shared-data, and ends by proving each as the bot; a host deployed before the
   bot fails it with the playbook to run there). A deploy that still
   lacks a setting says which, by name; without both tokens the bot is installed but
   stopped. Invite the app to every channel `slack_channels` names: it posts and pins in them and keeps their guides.

Who the bot knows comes from the people roster: `cluster_bot_members` (each person's
`slack` attribute) and `cluster_bot_admins` (people with the `admin` role).

## Files

| Path | What |
|---|---|
| `/usr/local/sbin/cluster-bot` | The administrators' command (`sudo cluster-bot notices [--dry-run]`): the bot's code as its user, with its unit's interpreter, path and config |
| `/etc/cluster-bot/config.json` | Channels, members, admins, feature settings (0640, the bot's group) |
| `/etc/cluster-bot/bot.token`, `app.token` | The tokens, read by the bot alone; never on a command line |
| `/etc/cluster-bot/github-app.pem` | The GitHub App's private key, read by the bot alone |
| `/etc/cluster-bot/known_hosts` | The host keys of the hosts it reaches, read from each host |
| `/var/lib/cluster-bot/` | Its SSH key and state: held requests, reminders sent, bans seen, shared data notices, docs notices posted and pinned, the channel guides' canvases and bookmarks (kept on removal) |
| `/etc/cluster-bot-access/<service>.json` | On a host it acts on: a service offered to it |
| `/var/lib/cluster-bot/.ssh/authorized_keys` | On a host it acts on: its key, for the dispatcher only |
