# New SSH sessions per person on the login nodes

`roles/ssh_rate` limits how many new SSH sessions each person opens on a login node.
One SSH connection per command (a script or an agent running each command over its own
`ssh`, a file copied per connection, `watch` over ssh) makes sshd, PAM, logind and the
directory do a whole login each time, for everyone on the node. A ControlMaster
connection carries any number of commands and is counted once.

Switch: `slurm_enable_ssh_rate` (component `ssh_rate`, on `slurm-login`). Playbook:
`playbooks/generic/ssh-rate.yml`. Keep a root session open on the host while it runs,
and log in again from a new connection before closing it.

## The rule

| Setting | Default | |
|---|---|---|
| `ssh_rate_mode` | `refuse` | `warn` counts, shows and tells, never refuses |
| `ssh_rate_refuse_sessions` | 100 | new sessions within the window that refuse a new login |
| `ssh_rate_window_minutes` | 10 | |
| `ssh_rate_block_minutes` | 10 | how long a refusal lasts |
| `ssh_rate_block_max_minutes` | 60 | the longest a retrying client is kept out |
| `ssh_rate_exempt_users` | the roster's administrators | never counted |

The warning comes at `cluster_user_warn_fraction` of the refuse level, the level every
rule warns at. Past the refuse level a new login is refused for `ssh_rate_block_minutes`.
A client that keeps trying while refused (as many attempts as the refuse level within
the window) is kept out for another block, up to `ssh_rate_block_max_minutes` from its
start, so a person who stops is free when the block ends and only a loop stays out.

Never counted or refused: root, the system's accounts (below `UID_MIN` of
/etc/login.defs: the services), `ssh_rate_exempt_users`, every PAM service other than
sshd (sudo, su, cron), and everything over an open connection: the channels of a
ControlMaster connection, scp, rsync and sftp through it, and open sessions.

## How it works

`cluster_tools.ssh_rate` is the one decision, run by `pam_exec` from a pam-auth-update
profile (`deepops-ssh-rate`, through `roles/pam_profile`, the only writer of the
`common-*` files): at session open it records the time in the person's file under
`/run/deepops/ssh-rate` (root's, a tmpfs, locked), and at account time it decides. The
gate (`/usr/local/libexec/ssh-rate-gate`) acts on sshd alone, runs the decision under a
2 second timeout and makes only its refusal (exit 77) a failure: an error, a timeout or
a missing interpreter lets the login in. The PAM line runs the gate through `/bin/sh`
only when it is there and executable, so a missing gate lets every login in too.
pam_exec has no timeout of its own: the gate, the decision and its interpreter must be
on local disk, and the role refuses to deploy when one is on NFS or another network
filesystem. A refusal is logged (`journalctl -t ssh-rate`).

What a refused person sees: sshd shows the message (`새 SSH 접속이 잠시 막혔습니다: ...`,
with the count, when it opens again and how to turn on ControlMaster) to a password
login only; a key login sees "Connection closed". So a refusal is never silent:

- the person gets a DM when it starts, and one at the warning level, again daily while
  it lasts (`slurm-usage-report --ssh`, every 5 minutes on the report's host);
- the administrators get one line per refusal in their channel and the alert
  `SshLoginRefused` (`SshSessionRateHigh` at the warning level, info);
- `usage login-node`, `/cluster usage` and the bot's Home tab show the row
  "SSH 접속 빈도" (cluster_tools.rules `ssh-rate`).

A timer (`ssh-rate-metrics`, every minute) publishes each person's count and any
refusal (`ssh_rate_new_sessions`, `ssh_rate_blocked_until_seconds`; the count and the
time only, never an address) with the thresholds, which the alerts, the notices and
the rule all read. `SshRateMetricsStale` fires when they stop coming in.

## A flood, by address

`roles/ingress_policy` drops new SSH connections from one address outside the cluster's
networks past `ingress_policy_ssh_new_per_minute` (600; burst
`ingress_policy_ssh_new_burst`, 200; 0 turns it off), counted in `dropped_ssh_rate`. It
is a ceiling against a flood only, far above what a lab behind one address opens; the
per-person limit is this role's.

## Slurm's own limit

slurmctld already limits how fast each user may send it requests (`SlurmctldParameters`
`rl_enable`): `watch -n 0.5 squeue` gets slower answers, never more of them. People
watch the queue with `sq -i <seconds>` instead.
