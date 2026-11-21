# Cluster bot (Slack)

`roles/cluster_bot` runs the cluster's Slack app on the first `slurm-master`: slash
commands, forms and buttons for requests users make themselves. It connects to Slack in
Socket Mode, an outbound WebSocket, so no port is opened and no public URL is needed.
It runs as its own account (`cluster_bot_user`, from `system_accounts`) under a hardened
systemd unit, and acts on other hosts only through SSH keys each of which may run one
forced command there.

Features today: scratch allocations (`/scratch`, see [scratch-alloc.md](scratch-alloc.md)):
ask, extend, resize and release from a form; requests within the automatic limits are
applied at once, larger ones wait for an administrator's button in the admin channel;
owners get a DM `cluster_bot_remind_days` before an allocation ends, with an extend button.

Account requests (`/join`, `/leave`): a newcomer gives a login, a name, a team (the
accounting file's accounts without children, but `cluster_bot_join_exclude`) and an SSH
public key; a person leaving (or an administrator, for anyone) asks to give an account
back. Both are checked first, wait for an administrator's button, and then become a pull
request on the site configuration made with the roster's own editors
(`plugins/filter/people.py`, as `scripts/deepops/people.py add` and `leave`). An
administrator merges it and runs `slurm-cluster.yml --tags people`; the roster stays the
only source of accounts.

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

## Turning it on

1. Create the Slack app from the manifest (below; api.slack.com/apps, "From a manifest"),
   install it to the workspace, and create an app-level token with `connections:write`.
2. Vault both tokens in the site config: `slack_bot_token` (xoxb-, already used by the
   notifiers) and `slack_app_token` (xapp-).
3. Set `slurm_enable_cluster_bot: true` (and, for scratch, `slurm_enable_scratch_alloc:
   true` with the scratch server in `[scratch-server]`).
4. Run `playbooks/generic/cluster-bot.yml`, then `playbooks/generic/scratch-alloc.yml`
   (it installs the bot's public key on the scratch server); preview each with
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
| `/var/lib/clusterbot/` | Its SSH key and state: held requests, reminders sent (kept on removal) |
