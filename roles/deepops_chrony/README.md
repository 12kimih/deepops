<!-- Vendored into DeepOps from DeepOps/ansible-role-chrony, a fork of unxnn/ansible-role-chrony (its LICENSE kept), at commit c95f84431c8c3ed693e88b35c87edf792fcf152f (meta/.galaxy_install_info), since rewritten. -->
# deepops_chrony

Time sync with chrony (chrony.conf(5)), as a deepops component: `chrony_install` in
roles/deploy_defaults switches it, `deepops_component_removals` removes it (the OS's
own time service takes over; docs/deepops/component-state.md).

Every host takes time from `chrony_upstream`, or, with `chrony_serve_hosts` set, only
those hosts do and serve it to `chrony_serve_networks`; every other host then takes
time from them by their address inside the cluster. A serving host that loses all
upstream sources keeps serving (orphan mode), so the cluster stays in step.

| Variable | Default | Meaning |
|----------|---------|---------|
| `chrony_state` | `present` | present, absent or kept; a deploy sets it |
| `chrony_service_state` / `_enabled` | `started` / `true` | the service |
| `chrony_upstream` | three `pool N.pool.ntp.org` lines | chrony.conf source lines |
| `chrony_serve_hosts` | `[]` | inventory hosts that serve the rest |
| `chrony_serve_networks` | `cluster_networks` | whom a serving host answers |
| `chrony_serve_orphan_stratum` | `10` | stratum served with no upstream left |
| `chrony_config_logdir` | `/var/log/chrony` | chrony's log directory |

The configuration keeps the distribution's drop-in directories (`confdir`,
`sourcedir` on Ubuntu and Debian) and its leap second handling (`leapsectz`). The time
zone is roles/locale's (`locale_timezone`).

## License

MIT
