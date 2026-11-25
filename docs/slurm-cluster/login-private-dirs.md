# Each login's own /tmp

`roles/login_private_dirs` (switch `slurm_enable_login_private_dirs`, off by default)
gives every person's login on the login nodes (`slurm-login`) its own `/tmp` and
`/var/tmp` (`login_private_dirs_paths`), as Slurm gives each job its own
(`slurm_job_private_dirs`). What one person, or the agents they run, write there is
neither seen by nor in the way of anyone else.

## How

[pam_namespace(8)](https://man7.org/linux/man-pages/man8/pam_namespace.8.html) mounts
the person's instance over each directory when a login opens. Three files, each the
component's own:

| File | What it says |
|---|---|
| `/usr/share/pam-configs/deepops-login-private-dirs` | The pam-auth-update profile: `optional pam_namespace.so` in the interactive session stack (`common-session`) alone. `roles/pam_profile` turns it on, the one writer of `/etc/pam.d/common-*`. |
| `/etc/security/namespace.d/deepops-login-private-dirs.conf` | Each directory, its instances under `<dir>-inst/<login>`, and the accounts that keep the shared one (`login_private_dirs_excluded`: root and the service accounts of `system_accounts`). |
| `/etc/tmpfiles.d/deepops-login-private-dirs.conf` | The instance parents `/tmp-inst` and `/var/tmp-inst`, root's with mode 0000 as pam_namespace requires, cleaned as the host's own rule cleans the directory they stand in for (Ubuntu: `/tmp` emptied at boot and its files gone after 30 days unused; `/var/tmp` never). A person's instance is made at their first login after it is gone, with the directory's owner and mode (pam_namespace copies them: root, 1777), and only their logins reach it. |

## What sees which

| Who | `/tmp` they see |
|---|---|
| An SSH or console login | The person's own; all their logins share it, so `tmux attach` and `screen -r` work across logins |
| `sudo`, `sudo -i` in a login | The caller's (sudo runs the non-interactive stack; root is excluded) |
| cron, systemd services, the Docker daemon, the person's own systemd manager (`systemd-run --user`) | The shared `/tmp` |
| root and the service accounts' logins | The shared `/tmp` |

So `docker run -v /tmp/x:/x` from a login mounts the daemon's `/tmp/x`, not the
person's: give Docker a path under the home. Files are counted to their owner's quota on
the root filesystem as before.

## Safety

The module is `optional`: a login whose instance cannot be set up (a missing instance
parent, a failed mount) proceeds with the shared `/tmp` and logs why
(`journalctl -t sshd | grep pam_namespace`); it never refuses the login. The role
creates the instance parents and the namespace settings before it turns the profile on,
and checks the non-interactive stack has no `pam_namespace`.

`scripts/validation/validate_slurm.py` reports `private_tmp_in_login`: on a host whose
settings give the login its own `/tmp`, run from a new login, it fails when that login's
`/tmp` is the shared one.

## Deploying

```bash
ansible-playbook -K playbooks/generic/login-private-dirs.yml
```

A login already open keeps the shared `/tmp` until it ends, and a new login does not
find the tmux or screen sessions an older one started (their sockets are in the shared
`/tmp`). Deploy while nobody is logged in. Files left in the shared `/tmp` stay there,
out of the logins' view, until the host's cleanup or the next boot.

`absent` turns the profile off and removes the three files; logins open then keep their
instances until they end. The instance directories stay, with people's files in them;
an administrator moves what anyone still needs.

Debian family only (pam-auth-update).
