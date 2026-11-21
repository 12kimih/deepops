# Component state: off means removed

Status: design (2026-11-20, owner-approved direction). Roles move over in batches;
`tests/component-state/` enforces the rules for every role that has moved.

## The rule

The configuration is the desired state of every host. A deploy makes each host match
it, whatever was there before:

- A component whose switch is on is **present** on the hosts of its target group.
- A component whose switch is off, and any component on a host outside its target
  group, is **absent**: what deepops installed for it is removed.
- A converged rerun changes nothing (`changed=0`), in both states.
- A version change leaves only the new version: the old one's files, packages and
  units go in the same run.
- **Data is never removed** by a switch or a group change: databases, registry image
  stores, Prometheus and Grafana data, NFS export contents, homes, backups, caches a
  user filled. Only software, configuration, units and containers go. Turning a switch
  back on finds the data where it was.

## Interface (every component role)

```yaml
# roles/<role>/defaults/main.yml
<role>_state: present        # present or absent
```

- `tasks/main.yml` asserts the value (present, absent, or kept: do nothing), then
  includes `present.yml` or `absent.yml`.
- `absent.yml` undoes exactly what `present.yml` does, through the shared modules:
  packages through `host_baseline` (`tasks_from: absent`), files in shared directories
  through `recorded_files`, Helm charts through `helm_release` (`state: absent`),
  accounts never (an account owns files on shared storage).
- `absent.yml` acts only on what deepops recorded installing (next section), so it
  never purges a package the OS shipped (rsyslog, chrony, apache2) and costs one fact
  read on a host that never had the component.
- Variables that meant started/stopped (`prometheus_state`, `nvidia_dcgm_state` in the
  exporter role and others) are renamed `_service_state`, and their paired `_enabled`
  knobs `_service_enabled`, retired with pointers, so `_state` and `_enabled` never
  read like the component switch. The test reads `_state` in role defaults only
  (`helm_release.state` is an argument, not a setting).

## The component record

`roles/component_record` keeps `/etc/ansible/facts.d/deepops_components.fact` on each
host. It is the host's own, like `deploy_record`, so it stays right after a limited run
or a run from another operator's machine.

- **Items have owner sets.** For every package, unit, path, container and fstab entry
  (`mount:<mountpoint>`), the record
  holds the set of components that need it, not a per-component list. A component
  going absent leaves every set; only items whose set became empty are removed
  (packages through `host_baseline`). apache2 for OOD, python3-kubernetes for the chart
  roles, dkms and headers for the driver and MOFED, Docker for everything that runs
  containers: no special cases, and nothing another component needs is purged. An
  orphaned fstab entry is removed from fstab, never unmounted: the live mount stays
  until the next boot (unmounting under logged-in users fails or hangs).
- **Intent first.** `present.yml` records its component as `installing` with the items
  it will add before it changes anything, and `present` with its version after; a run
  that fails midway leaves items absent can still find.
- **Adoption.** Every role has a cheap detect step (dpkg-query of its packages, its
  units and paths). On its first run with the framework, present or absent, what it
  finds is adopted into the record, as `build_record` adopts existing builds. Hosts
  deployed before the record existed, and leftovers installed by hand, are cleaned up
  like anything recorded. Without a record, absent takes only what shows deepops made
  it: a mark in a file (`plugins/module_utils/deepops_marks.py`), or, for a file an
  earlier version wrote unmarked, its whole content matching that version's shape
  (`<role>_legacy` in the role's vars/main.yml, passed as `component_record_legacy`:
  regexes anchored on deepops' own text, so a site's file of the same name or one
  edited by hand never matches).
- **Versions.** A changed version removes the items only the old version owned.
- **absent** starts from the record and the detect step: nothing found, nothing to do
  (`end_role`). After removal the component's entry goes.
- **Check mode.** absent previews (host_baseline already lists what apt would purge)
  and nothing writes the record under `--check`.

## One desired-state map

A component registry (one data file, `roles/deploy_defaults/vars/main.yml`)
lists each component once: its role, the switch of each deploy that turns it on, its
target group in each deploy, and the components it `needs`. One filter computes, per
host and once per run, the map component -> present or absent from every switch and
group of both deploys, so a host in the Slurm and the Kubernetes deploy keeps what
either wants. A component another present component `needs` is present (Docker, the
container toolkit: no switch of their own, gone when nothing needs them; their data,
`/var/lib/docker`, stays).

The switch decides the state, never whether the play runs. Plays run on every host of
the deploy and read their state from the map:

```yaml
# playbooks/slurm-cluster.yml
- name: Install rsyslog server
  import_playbook: generic/rsyslog-server.yml

# playbooks/generic/rsyslog-server.yml: every host of the deploy converges
- hosts: "{{ hostlist | default('slurm-cluster') }}"
  roles:
    - role: deploy_defaults
    - role: rsyslog_server
      rsyslog_server_state: "{{ deepops_components.rsyslog_server }}"
```

A component the site manages itself (`managed: false` in its entry, such as Docker with
`docker_install: false` on DGX OS) is `unmanaged`: every phase passes `kept` to its role.
A playbook that needs a component on its own hosts whatever the map says
(`ngc-ready-server.yml` needs Docker) imports it with `deepops_phase: require`.

## Order

Each component play passes its state through `component_phase(deepops_phase)`. Run alone
it converges (`converge`). A deploy imports it twice: with `install` in the install
order, where an absent component waits (`kept`), and with `remove` in a final phase,
where only absent components act. Installs follow the registry's `needs` graph (a
topological order) and removals its reverse, which the test checks: pyxis before enroot before the container toolkit;
exporters, then their dashboards, then Alertmanager, then Prometheus and Grafana; usage
report and metrics before cluster_tools_lib; HPC SDK registration before Lmod; NFS
clients before the server.
- Reboot-class and access-class components never go implicitly: the NVIDIA driver,
  authentication (NIS, the directory, Kerberos), fail2ban (its removal lifts every
  ban), Slurm itself and MUNGE, and the NFS server (unexporting /home hangs every
  client, as on 2026-11-18). Their registry entry
  says `removal: explicit`; where no rule holds the map gives `kept` and the role does
  nothing. The site retires one by naming it in `deepops_component_removals`: it goes
  from every host no rule wants it on and stays where one does. Naming a component
  without `removal: explicit` stops the deploy.
- Retiring a component is a code fact, not a site choice: its registry entry says
  `retired: "<why, since when>"`, has no rules or needs, and nothing needs it. It is
  absent on every host whatever `removal` says (a site-managed one stays unmanaged), so
  its role's absent path runs everywhere, adopting what deployments before component
  records left; a deploy imports its play in the removal phase only. Once every site
  has converged, one commit deletes the entry, the role and the play, with
  `retired_settings` entries for any of its settings a site may still set.

## What absent must undo besides packages

- Files a component drops into another's directory: an exporter's file_sd endpoint
  file on the Prometheus host (left behind, ExporterDown fires forever); the
  dashboards it provisions through `grafana` `tasks_from: dashboards`; every textfile
  writer's `.prom` file in the node exporter's directory and its persistent copy
  (slurm_metrics, slurm_backup, os_updates, slurm_usage_report, egress_policy). These
  go through `recorded_files`, so the owner of the directory keeps the rest.
- Data and configuration never share a directory: Grafana's provisioned dashboards
  move from `/var/lib/grafana` (its database: data) to `/etc/grafana/dashboards`
  (configuration), once, in its role.
- Validators treat a component that is absent by design as absent, not as a broken
  rollout.

## Not components

- Migrations and one-shot settings have no absent state: move-home-dirs, hostname and
  /etc/hosts, swapoff, bootstrap-python, Kubespray itself.
- **Policies** are enums, not present/absent: os_updates (security, off, unmanaged:
  off disables unattended upgrades, it does not purge the package), node_user_policy,
  ai_agent_rules, and `disable-cloud-init` (off re-enables cloud-init). The test keeps
  them on an explicit list and checks each value has its own converging path.

## Enforcement (tests/component-state)

- Every role in the registry has `<role>_state` in its defaults, an `absent.yml`, a
  detect step, and calls `component_record` in both paths.
- Every deploy switch is in the registry; no import is skipped on `when: <switch>`,
  and the playbooks' order agrees with the `needs` graph. A switch a deploy still
  gates an import on is listed in the test's `GATES` with why: a policy, a one-shot
  setting, or a component still to move with its owner. The list only shrinks: a name
  no gate uses fails the test.
- No `absent.yml` removes a path listed as data in its role's defaults
  (`<role>_data_paths`), or any directory recursively unless the record names it.
- A molecule scenario per batch runs present, rerun (`changed=0`), absent, rerun
  (`changed=0`), present again (data still there).

## Batches

1. Framework: `component_record`, the registry and its map, the test, the
   `_service_state`/`_service_enabled` renames; pilot on rsyslog_server, rsyslog_client,
   nvidia_power_limit, cluster_sudoers (deepops-4).
2. Monitoring (prometheus, grafana with the dashboards move, alertmanager, the
   exporters, slurm_metrics: deepops-3); containers (registry, nginx cache, Docker as a
   derived dependency: deepops-4).
3. GPU and HPC software: nvidia_dcgm, nhc, openmpi, lmod, nvidia_hpc_sdk,
   nvidia_cuda_toolkit, easy-build, podman, enroot and pyxis, the NVIDIA driver
   (removal: explicit) and the container toolkit (needed by enroot and the DCGM
   exporter) (deepops).
4. Services: open_ondemand, nfs (the exports), nfs_client (the mounts), cluster_tools
   (deepops-2);
   egress_policy and the policies os_updates, node_user_policy, ai_agent_rules
   (deepops-4).
5. Kubernetes charts: already on `helm_release` with `state`; map their switches.
