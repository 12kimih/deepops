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
| `/etc/cluster-bot/known_hosts` | The host keys of the hosts it reaches, read from each host |
| `/var/lib/clusterbot/` | Its SSH key and state: held requests, reminders sent (kept on removal) |
