# Naming

One thing has one name, written the way each place spells names: a role's words are
the same in its directory, its variables, its commands, units and files, its metrics and
its alerts, the way a login's home is `/home/<login>`. `tests/naming/test_naming.py`
holds the repository to these rules; what does not follow them yet is listed there, each
with what it is to become, and the list only shrinks.

| Thing | Form | Example (role `disk_quota`) |
|---|---|---|
| Role directory, component key (`roles/deploy_defaults/vars/main.yml`), variable prefix | snake_case | `disk_quota`, `disk_quota_limits` |
| Its switch | `slurm_enable_<key>` (`k8s_enable_<key>` on Kubernetes) | `slurm_enable_disk_quota` |
| Its playbook | `<key-kebab>.yml` | `playbooks/generic/disk-quota.yml` |
| A cluster tool it runs | the module `cluster_tools.<key>`, the command `<key-kebab>` | `cluster_tools.disk_quota`, `/usr/local/libexec/disk-quota` |
| Its systemd units | `<key-kebab>[-<what>].service`, `.timer` | `disk-quota.service` |
| Its configuration | `/etc/cluster-tools/<key-kebab>.json` | `/etc/cluster-tools/disk-quota.json` |
| Its state | `/var/lib/<key-kebab>/` (systemd StateDirectory=) | `/var/lib/slurm-usage-report/` |
| Its textfile metrics | `<key-kebab>.prom`, every metric `<key>_...` | `disk_quota_used_bytes` |
| When it last worked | `<key>_last_success_timestamp_seconds` | `disk_quota_last_success_timestamp_seconds` |
| Its alert rules | `roles/prometheus/files/rules/<key-kebab>.yml`, alerts `<Key>...` in CamelCase | `DiskQuotaStale` |
| A service account | its name; its home `/var/lib/<name>`, or `/home/<name>` when the home is a shared space people use | `cluster-bot`... `shared` (`/home/shared`) |

Where a command goes: run by a timer, `/usr/local/libexec`; run by people,
`/usr/local/bin`; run by root, Slurm or the cluster bot, `/usr/local/sbin`.

## Renaming something deployed

A role's record (roles/component_record) lists what it installed; on the next deploy
everything the record has and the role no longer lists is removed. So a renamed unit,
file or directory needs nothing more than the new name in the role's items: the old one
goes. A renamed component (its key) also gets a `retired:` entry under its old key in the
registry, so hosts that have it remove it. Rename a metric, an alert and every rule and
dashboard that reads it in one commit, and say in the commit that its history starts
again under the new name.
