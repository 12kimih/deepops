# Slurm Monitor

- [Slurm Monitor](#slurm-monitor)
  - [Introduction](#introduction)
  - [Exporters](#exporters)
  - [Grafana](#grafana)
  - [Prometheus](#prometheus)
  - [Alertmanager](#alertmanager)
  - [Slack usage report](#slack-usage-report)

## Introduction

By default DeepOps deploys a monitoring stack alongside Slurm. This can be disabled by setting `slurm_enable_monitoring` to `false`.

DeepOps runs a `dcgm-exporter` container on every GPU node. This container exports various GPU utilization and metadata to a Prometheus database running on the `slurm-metric` nodes. The `slurm-metric` nodes also run a Grafana server that connects to the Prometheus database to visualize the data. Ad-hoc metric queries can be made against the Prometheus server, but monitoring is typically done through the dashboard on Grafana.

## Exporters

Prometheus renders its scrape targets from the component map
(`roles/deploy_defaults/vars/main.yml`; `prometheus_exporters` in `roles/prometheus_paths`
gives each exporter's port): an exporter is scraped exactly where it is present, so one
turned off or moved stops being scraped. Prometheus adds a `host` label (the target
address without its port) so queries and alerts can join exporters:

| Exporter | Runs on | Port |
|---|---|---|
| `node-exporter` | every Slurm host | 9100 |
| `dcgm-exporter` | GPU nodes (`slurm-node`) | 9400 |
| `ipmi-exporter` | every Slurm host; reads hosts with `/dev/ipmi0` | 9290 |
| slurmctld (Slurm itself) | the controller | `slurm_slurmctld_port` (6817) |

Slurm's metrics come from slurmctld itself (`MetricsType=metrics/openmetrics`): Prometheus
scrapes its `/metrics/<set>` endpoints for each set in `prometheus_slurmctld_metrics`
(jobs, nodes, partitions, scheduler). The metric names are Slurm's own; the tests check
every name the Slurm dashboard reads against the pinned release's
(`tests/cluster-tools/slurm-names.json`, from `scripts/deepops/slurm_names.py`). The Go
exporter deepops used before is retired: a deploy removes it from every host.

`ipmi-exporter` reads the BMC in-band and provides whole-system power draw
(`ipmi_dcmi_power_consumption_watts`), PSU, fan and temperature sensors. The GPU dashboard
falls back to it when a node has no ACPI power meter (`node_hwmon_power_average_watt`).
Install it on its own with `playbooks/slurm-cluster/prometheus-ipmi-exporter.yml`.

The node and GPU exporter containers look like this:

```bash
# View the DCGM Exporter container on a GPU node
$ sudo docker ps
CONTAINER ID        IMAGE                              COMMAND                  CREATED             STATUS
PORTS                    NAMES
139fe402640f        quay.io/prometheus/node-exporter   "/bin/node_exporter ..." 56 minutes ago      Up 56 minutes                                docker.node-exporter.service
0da9e3f1a7c8        nvcr.io/nvidia/k8s/dcgm-exporter   "/usr/bin/dcgm-expor..." 56 minutes ago      Up 56 minutes       0.0.0.0:9400->9400/tcp   docker.dcgm-exporter.service
```

## Grafana

Grafana provisions these dashboards into one folder (`grafana_dashboard_folder`; name it
after the cluster). Names follow the monitoring-mixin form "Domain / View" and each has a
fixed UID, so links survive renames:

| Dashboard | UID | Shows | Installed by |
|---|---|---|---|
| GPU / Nodes | `gpu-nodes` | one GPU node: GPUs (DCGM), host resources, system power | `grafana` |
| Slurm / GPU allocation | `slurm-gpu-allocation` | who holds which GPU, by user, account, type and node; each job's GPU utilisation and memory; GPUs busy with no job | `slurm_metrics` |
| Slurm / Scheduler | `slurm-scheduler` | nodes, CPUs, GPUs, jobs and scheduler statistics (slurmctld's metrics) | `grafana` |
| Node Exporter / Hosts | `node-exporter-hosts` | per-host detail (grafana.com 1860 "Node Exporter Full", revision 46) | `grafana` |

The dashboards deepops builds are Python (`scripts/dashboards/`, shared building blocks in
`grafana.py`); `scripts/dashboards/build.py` writes the JSON the roles ship, and the
checks fail when that JSON is stale. Edit the Python, never the JSON. Node Exporter / Hosts
is the upstream file, kept as is.

Each role installs its dashboards into its own subdirectory and removes the ones it no
longer ships; Grafana then deletes those, so the files are the only source of truth.
The folder has a fixed UID (`grafana_dashboard_folder_uid`). File provisioning does not
repair everything itself (it never moves a dashboard when the folder changes, renames the
folder, or removes a dashboard it stopped managing), so the grafana role finishes with
`grafana_reconcile.py`, which converges those and deletes `grafana_retired_dashboards`;
`--check` shows what it would do.
The Prometheus datasource has the fixed uid `prometheus`.

Anonymous visitors get read-only access (`grafana_anonymous_role`). The admin password is
`grafana_cfg_pass`, enforced on every run even on an existing database; vault a real one in
`config/`.

In order to gain access to Grafana visit the url at http://\<slurm-metric ip\>:3000/ (`grafana_port`). Once the web page loads, the dashboards can be accessed by clicking the dashboards icon on the left and selecting manage. This will bring you to a page listing all available dashboards. Click on a dashboard's name to view it.

![Grafana Home](../img/slurm_monitoring_grafana01.png)

![Grafana Select Dashboards](../img/slurm_monitoring_grafana02.png)

![Grafana Dashboards](../img/slurm_monitoring_grafana03.png)

GPU / Nodes shows GPU power, temperature, utilisation, memory-copy utilisation, memory and
clocks per GPU and in total, beside the node's load, memory (as `free` splits it), disk,
Ethernet, InfiniBand and NVLink traffic and system power. A gap in a line means no data,
never zero.

Metrics are displayed per-node. To view utilization across nodes click the server selection drop-down in the top left and type or select the desired node names.

![Grafana GPU Dashboard](../img/slurm_monitoring_grafana04.png)

![Grafana GPU Node Selection](../img/slurm_monitoring_grafana05.png)

Slurm / Scheduler provides the following information about running jobs and the Slurm job queue:

- Nodes & Node Status
- Slurm Jobs & Job State
- Slurm Agent Queue Size
- Scheduler Threads & Backfill Depth Mean & Cycles

![Grafana Slurm Dashboard](../img/slurm_monitoring_grafana06.png)

Slurm / GPU allocation comes from `roles/slurm_metrics`: a timer on the controller writes
`slurm_gpu_allocated{node,gpu,gpu_type,user,account,partition,job_id}` (1 per GPU a job
holds) and `slurm_gpu_configured{node,gpu_type}` every minute as a node_exporter text file. The
recording rule `host_device:slurm_gpu_allocated:max` keys them like DCGM series, and
`host_device:slurm_gpu_unheld:info` names the GPUs no job holds on nodes in service. The
alert `GpuBusyWithoutJob` fires when one of those stays busy for 15 minutes (a process
outside Slurm, or one a finished job left behind), and `GpuStuck` (critical) when it is
fully busy with almost no memory in use for 10 minutes: a hung GPU, which needs a reset or
a reboot. `SlurmGpuMetricsStale` warns when the file stops updating, which also pauses
both alerts.

Per-job GPU usage comes from the same join: `job_device:dcgm_gpu_util_ratio:max` and
`job_device:dcgm_fb_used_bytes:max` carry each held GPU's utilisation and memory, and
`job_device:dcgm_sm_util_ratio:max` and `job_device:dcgm_power_watts:max` its SM activity
(the share of cycles an SM has work, which utilisation alone does not tell) and board
power, with the job's labels (`job_id`, `user`, `account`, `partition`). The dashboard's "by job" panels
show them, with each job's mean and peak over the selected range. They cover jobs that ran
for at least one allocation interval (a minute), for as long as Prometheus keeps data.
Slurm's own accounting (`sacct` gres/gpuutil, gres/gpumem) records them only with
`AutoDetect=nvml` in gres.conf.

`gpu-usage JOB` (a `cluster_tools_commands` entry) prints one job's figures, running or
finished: Slurm's per step where the job has them, else Prometheus' per GPU, each row
naming its source (`--source slurm|prometheus` asks for one, `--json` for scripts). The
two differ in meaning: Slurm keeps each task's high-water mark, Prometheus the mean and
max over the run. A line under each job gives the share of its allocated CPUs it kept
busy (its CPU time over CPUs times elapsed time, as `seff` computes it; Slurm adds a
step's CPU time when the step ends): a GPU job near 100% there while its GPUs idle is
held back by its CPUs. The job is looked up in every cluster the accounting database holds,
so a job from before a rename (kept under the old name) is found too; each report names
its cluster. It reads Prometheus at the site facts' `prometheus_url`, the first host of
the prometheus component, so every host running it must reach that port.

Grafana allows you to view custom time-slices, set the polling duration, and many other dashboard customizations.

For more information on creating custom dashboards and the available GPU metrics provided by Prometheus refer to the [DCGM Exporter README](https://github.com/NVIDIA/dcgm-exporter) or [Grafana home page](https://grafana.com/).

## Prometheus

In order to make ad-hoc queries Prometheus can be used. Visit Prometheus at http://\<slurm-metric ip\>:9090/graph and select one or more metrics in the drop down. Metrics can be visualized using the Graph button, or the text output can be shown by selecting the Console button. Advanced queries can be constructed using the Prometheus syntax. After constructing a query click the execute button and inspect the results.

![Prometheus Query](../img/slurm_monitoring_prometheus01.png)

For more information on constructing Prometheus queries see the official [Prometheus getting started guide](https://prometheus.io/docs/prometheus/latest/querying/basics/).

Data is kept for `prometheus_retention_time` (default `90d`) or until it reaches
`prometheus_retention_size` (default `50GB`), whichever comes first. Config and rule changes
are checked with `promtool` before they are written, then applied with a live reload.

After a monitoring deploy, `scripts/validation/validate_monitoring.py --json` (from a
checkout on any host that reaches Prometheus; read-only) must report `"ok": true`. It
checks that every target is up, that every rule the repository ships is loaded and every
rule group evaluates, that the Watchdog alert reaches Alertmanager, that every metric the
alert rules and dashboards read has samples in the last minutes, that every GPU joins
Slurm's device numbering, and that no staleness alert (below) fires. A rule not loaded,
or a metric missing while its source reports others, is a rollout gap: deploy that
component's playbook to its hosts.
Some metrics may legitimately be absent, and are listed under `absent_ok` instead
(`ABSENT_OK` in the script):

- metrics that only some hardware or kernels have (fans, PSUs, HBM temperature, IRQ pressure)
- collectors left off on purpose
- series that exist only while in use
- results that appear only after the first run

## Alertmanager

Prometheus evaluates the alert rules in `roles/prometheus/files/rules/`, grouped by area:
targets (a host is down only when none of its exporters answer; one silent exporter is
an exporter alert), host (disk, inodes, memory, clock, failed or unresponsive systemd,
ECC, RAID, NIC errors), GPU (XID, missing GPUs, row remapping, temperature, PCIe replays),
power (PSU, BMC sensors, and chassis intrusion as a warning, since it is a latched event cleared in the BMC) and the monitoring stack itself. Add site rules with
`prometheus_extra_rule_files`. Check rule state at http://\<slurm-metric ip\>:9090/alerts.
The alert-rules test files in `tests/monitoring/` (one per evaluation interval) unit-test the rules with `promtool test rules`.

Each timer that writes textfile metrics has a staleness alert on its last timestamp, and
that alert also covers a job that never reported at all. The Slurm backups count from when
each destination and the restore test were first configured, a time `slurm-backup` keeps.
The other jobs have a `...NeverReported` companion, which fires when the job's systemd
unit is installed on a host but its timestamp is missing for the same window. Without one
of these, a job that never ran or failed every run would stay silent.

The always-firing `Watchdog` alert proves the whole path works. Point
`alertmanager_heartbeat_url` at an external check (for example a healthchecks.io ping
URL with a 2 minute period and 5 minutes of grace): Alertmanager pings it about every minute,
and the external service alerts when the pings stop, which covers a dead monitoring host. Without the URL, Watchdog
is dropped.

Alertmanager groups alerts by `alertname` and `host`, repeats unresolved ones every
`alertmanager_repeat_interval` (critical: `alertmanager_critical_repeat_interval`), and
suppresses every other alert on a host while `HostDown` fires for it. To send alerts to
Slack, create an incoming webhook and set (vaulted) in `config/`:

```yaml
alertmanager_slack_api_url: "https://hooks.slack.com/services/..."
alertmanager_slack_channel: "#alerts"
alertmanager_slack_critical_mention: "<!subteam^S0123ABCD>"   # optional
```

The config is checked with `amtool` before it is written. Visit
http://\<slurm-metric ip\>:9093 to view and silence alerts.

## Slack usage report

`playbooks/slurm-cluster/slurm-usage-report.yml` installs a systemd timer on the
monitoring host that posts a GPU usage report to Slack every hour
(`slurm_usage_report_schedule`). The top-level message is a summary card (GPUs allocated,
average utilisation, idle jobs, pending jobs, drained nodes, and a link to
`grafana_root_url`); the thread lists idle jobs, drain/down nodes with
reasons, pending jobs per user, and every running GPU job per user as a table with a
utilisation bar and GPU memory.

A job counts as idle when it has run for `slurm_usage_report_idle_hours` and every GPU it
holds stayed at or under `slurm_usage_report_idle_util_threshold` percent the whole time
(DCGM `DCGM_FI_DEV_GPU_UTIL_RATIO`, shown in percent). `slurm_usage_report_notify` lists how its owner hears about
it: `mention` in the report thread, `dm`, both, or `[]` for neither. Owners are notified
when a job first turns idle and then every `slurm_usage_report_notify_repeat_hours`, not on
every report; the thread lists idle jobs either way.
`slurm_usage_report_cancel_after_hours` cancels idle jobs after that long (0 = never) and
says so in the report thread. A job is cancelled only after its owner was notified in an
earlier report, so the setting must exceed `slurm_usage_report_idle_hours` and needs
`slurm_usage_report_notify`; the deploy checks the config and stops on a mistake.

The role needs a Slack app bot token (`slack_bot_token`, scopes `chat:write`, `im:write`,
`users:read`, `users:read.email`) invited to the channel. Slurm users map to Slack
members through `slurm_usage_report_user_map`, or by email with
`slurm_usage_report_email_domain`. Preview a report without notifying anyone, either
printed or posted to one person's DM (or any channel):

```bash
sudo /usr/local/libexec/slurm-usage-report --dry-run
sudo /usr/local/libexec/slurm-usage-report --to U0123ABCD
```
