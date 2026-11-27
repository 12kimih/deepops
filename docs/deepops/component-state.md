# Component state: off means removed

`tests/component-state/` enforces these rules for every component in the registry.

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
  it: a name or text that says deepops or names the role (`# Managed by roles/<role>`;
  `plugins/module_utils/deepops_marks.py`: a bare `Ansible managed` is any Ansible's,
  a site's too, so it never counts for a removal), or, for a file an
  earlier version wrote unmarked, its whole content matching that version's shape
  (`<role>_legacy` in the role's vars/main.yml, passed as `component_record_legacy`:
  regexes anchored on deepops' own text, so a site's file of the same name or one
  edited by hand never matches).
- **Versions.** A changed version removes the items only the old version owned.
- **absent** starts from the record and the detect step: nothing found, nothing to do
  (`end_role`). After removal the component's entry goes.
- **Check mode.** absent previews (host_baseline already lists what apt would purge)
  and nothing writes the record under `--check`.
- **Seen by Prometheus.** roles/os_updates publishes each record
  (`os_updates_component_info`, `os_updates_package_component_info`,
  `os_updates_component_source_info`) as soon as a deploy writes it: component_record
  then runs what exports the record (`deepops_component_record_readers`, through
  roles/textfile_keep `tasks_from: refresh`), and the daily measurement does too. And
  `scripts/deepops/component_playbooks.py` generates each component's playbook as a
  series (`roles/prometheus/files/rules/components.yml`): a component at two versions
  across its hosts (a run limited to some of them) or a local package at two versions
  alerts with each host's version and the playbook that brings them level.
- **Behind the repository.** Hosts can agree and still be behind: a pin bumped, the
  checkout synced, the playbook not run. `scripts/deepops/component_expected.py` reads
  each role's own `component_record_version` expression and resolves it per host from
  the role defaults and the site config (the settings.py model). The result is the
  expected component state, a node_exporter textfile on the Prometheus host
  (`component_expected_dir`, the site's resolved state on its own host, never in the
  repository): every prometheus.yml run writes it, and so does `scripts/deepops/sync.py`
  after each move of the checkouts, so it follows the checkouts without a playbook run.
  Its directory is writable by the controller user's group only when that is the user's
  own private group on the host (whoever can write it can silence or spoof the alerts
  that read it); otherwise it stays root's, the run says so, and only prometheus.yml
  writes it.
  `ComponentVersionNotPinned` names each host whose recorded version is another, with
  the playbook that installs it. A component whose version only the host can work out
  (a fact, a value set at run time) is named in the head of that file, unchecked, with
  the reason.
- **Fixed but never deployed.** The same version can still run older code: a role fix
  that only limited runs reached since leaves the other hosts on the old one, and their
  deploy record looks current. Each whole deploy records on the host a source digest
  (`scripts/deepops/component_source.py`) for every managed role its plays applied, a
  component or not (next section): its role's files; of the roles it pulls in, what of
  them runs (the tasks files reached, their handlers, templates, files and modules) and
  the defaults that code reads, of the component map only the entries it names; the
  playbook that applies it; the in-tree plugins and repository files those name; and the
  config values of the settings its code reads (another role's too, and through hostvars
  every host's), of the settings those values name, and the config files they name.
  Files count by git blob id, so an unrelated commit changes nothing and a working tree
  with uncommitted changes counts as it is. component_record itself is left out of every
  closure and has its own digest (`record_source`). `ComponentNotDeployed` names each
  host, after three days, whose recorded digest is not what the checkouts give now, with
  the playbook; a host with no digest yet is not compared. A shared plugin is code of
  every component that uses it, so a change there flags them all at once (one
  notification, grouped by alert): the whole deploy brings them level.
  `scripts/deepops/sync.py` prints which hosts' expected versions and sources a move
  changed and the playbooks to run; `component_expected.py changes --from REV` says the
  same for any revision.

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

## What a deploy records

Every role a play applies whole that changes a host has an entry: a component in
`deepops_component_registry`, every other one in `deepops_managed_roles` beside it (same
file), with its kind and the one playbook that brings its hosts level:

| kind | what it is | examples |
|---|---|---|
| component | present or absent, the interface above | chrony, prometheus, cluster_tools |
| policy | a value the role keeps (an enum, a mode); another value instead of absent | os_updates, node_user_policy, ai_agent_rules, nvidia_ecc |
| oneshot | a migration or a setting made once | deepops_hosts, move-home-dirs, site_secrets |
| permanent | converged on every run, no removal path yet | slurm (with MUNGE), sshd, people, host_baseline, mofed |
| operation | a step run on request; registered, never recorded | maintenance, maintenance_window, nvidia-dgx-firmware |

Only components enter the present/absent map: the other kinds are never removed by a
switch. Whole means the play's `roles:` or an include_role/import_role in its tasks
without `tasks_from`; a role used only in part (`tasks_from`) is a library of the role
that uses it, and so are roles named `*_defaults` or `*_paths` and roles of only checks
and facts.

The record is one step, the last post_task of every play that applies an entry
(`component_record` `tasks_from: sources`, with `component_record_sources_also` naming
the roles the play's tasks include whole). It flushes the handlers first, so it runs
after the play's roles, tasks and handlers, and records for each host still in the play
the digest of every entry the play applied: a host that failed a task or a handler, or
was unreachable, left the play and records nothing; `--limit` records the hosts it ran;
`--tags`, `--skip-tags` and `--check` record nothing, keeping what was recorded before. A
component's digest is written only while its record says present (an absent one is
never brought back); the other kinds' go beside the components (`managed`). No role
records its own digest. `tests/component-state/test_sources.py` fails a play that
applies an entry without the step as its last post_task, a whole role a play applies
that has no entry, and an entry whose playbook does not apply it.

A role's coverage starts with its first whole run: until then it has no digest and
ComponentNotDeployed does not compare it. Slurm and MUNGE are `permanent`, not yet
components: their absent path, which makes them a component with `removal: explicit`,
comes with the rework of the Slurm templates, and a run of slurm.yml limited by `--tags`
(the prolog and epilog) records nothing, so a fix there stays unlisted until slurm.yml
runs whole.

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
  ban), Slurm itself and MUNGE once they are components (permanent until then: What a
  deploy records), and the NFS server (unexporting /home hangs every
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
  writer's `.prom` file in the node exporter's directory and its persistent copy. These
  go through `recorded_files`, so the owner of the directory keeps the rest.
- Data and configuration never share a directory: Grafana's provisioned dashboards
  move from `/var/lib/grafana` (its database: data) to `/etc/grafana/dashboards`
  (configuration), once, in its role.
- Validators treat a component that is absent by design as absent, not as a broken
  rollout.

## Not components

Each of these that a play applies as a role has a `deepops_managed_roles` entry of its
kind (What a deploy records), so a fix to it is tracked like a component's.

- Migrations and one-shot settings have no absent state: move-home-dirs, hostname and
  /etc/hosts, swapoff, bootstrap-python, Kubespray itself.
- **Policies** are enums, not present/absent: os_updates (security, off, unmanaged:
  off disables unattended upgrades, it does not purge the package), node_user_policy,
  ai_agent_rules, and `disable-cloud-init` (off re-enables cloud-init). The test keeps
  them on an explicit list and checks each value has its own converging path.

## Enforcement (tests/component-state)

- Every role in the registry has `<role>_state` in its defaults, an `absent.yml`, a
  detect step, and calls `component_record` in both paths.
- Every role a play applies whole that changes a host has an entry, and every play that
  applies one ends with the record (test_sources.py).
- Every deploy switch is in the registry; no import is skipped on `when: <switch>`,
  and the playbooks' order agrees with the `needs` graph. A switch a deploy still
  gates an import on is listed in the test's `GATES` with why: a policy, a one-shot
  setting. The list only shrinks: a name
  no gate uses fails the test.
- No `absent.yml` removes a path listed as data in its role's defaults
  (`<role>_data_paths`), or any directory recursively unless the record names it.
- A molecule scenario per component group runs present, rerun (`changed=0`), absent, rerun
  (`changed=0`), present again (data still there).
