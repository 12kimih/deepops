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

Each exporter role writes its scrape targets to `/etc/prometheus/endpoints/` (file_sd), and
Prometheus adds a `host` label (the target address without its port) so queries and alerts
can join exporters:

| Exporter | Runs on | Scrape targets | Port |
|---|---|---|---|
| `node-exporter` | all hosts | `node_exporter_host_group` (default `slurm-cluster`) | 9100 |
| `dcgm-exporter` | GPU nodes | `slurm-node` | 9400 |
| `ipmi-exporter` | hosts with `/dev/ipmi0` | `ipmi_exporter_host_group` (default `slurm-cluster`) | 9290 |
| `slurm-exporter` | `slurm-metric` | `slurm_exporter_host_group` (default `slurm-metric`) | 8080 |

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

Grafana ships three dashboards in the `Cluster` folder: the GPU dashboard, the Slurm
dashboard, and Node Exporter Full (grafana.com 1860, revision 46) for per-host detail.
The Prometheus datasource has the fixed uid `prometheus`.

Anonymous visitors get read-only access (`grafana_anonymous_role`). The admin password is
`grafana_cfg_pass`, enforced on every run even on an existing database; vault a real one in
`config/`.

In order to gain access to Grafana visit the url at http://\<slurm-metric ip\>:3000/ (`grafana_port`). Once the web page loads, the dashboards can be accessed by clicking the dashboards icon on the left and selecting manage. This will bring you to a page listing all available dashboards. Click on a dashboard's name to view it.

![Grafana Home](../img/slurm_monitoring_grafana01.png)

![Grafana Select Dashboards](../img/slurm_monitoring_grafana02.png)

![Grafana Dashboards](../img/slurm_monitoring_grafana03.png)

The GPU dashboard provides the following metrics:

- System Load
- GPU Power Usage (by GPU)
- GPU Power Total
- System Memory Usage
- GPU Temperature (by GPU)
- GPU Average Temperature
- PCIe Throughput
- Disk Usage
- GPU Memory Usage
- GPU Utilization (by GPU)
- GPU Total Utilization
- Ethernet Throughput
- Disk Throughput
- GPU Mem Copy Utilization
- GPU Average Memory Copy Utilization
- Kernel
- Hostname
- System Total Power Draw
- GPU SM Clocks
- GPU Memory Clocks

Metrics are displayed per-node. To view utilization across nodes click the server selection drop-down in the top left and type or select the desired node names.

![Grafana GPU Dashboard](../img/slurm_monitoring_grafana04.png)

![Grafana GPU Node Selection](../img/slurm_monitoring_grafana05.png)

The Slurm dashboard provides the following information about running jobs and the Slurm job queue:

- Nodes & Node Status
- Slurm Jobs & Job State
- Slurm Agent Queue Size
- Scheduler Threads & Backfill Depth Mean & Cycles

![Grafana Slurm Dashboard](../img/slurm_monitoring_grafana06.png)

Grafana allows you to view custom time-slices, set the polling duration, and many other dashboard customizations.

For more information on creating custom dashboards and the available GPU metrics provided by Prometheus refer to the [DCGM Exporter README](https://github.com/NVIDIA/dcgm-exporter) or [Grafana home page](https://grafana.com/).

## Prometheus

In order to make ad-hoc queries Prometheus can be used. Visit Prometheus at http://\<slurm-metric ip\>:9090/graph and select one or more metrics in the drop down. Metrics can be visualized using the Graph button, or the text output can be shown by selecting the Console button. Advanced queries can be constructed using the Prometheus syntax. After constructing a query click the execute button and inspect the results.

![Prometheus Query](../img/slurm_monitoring_prometheus01.png)

For more information on constructing Prometheus queries see the official [Prometheus getting started guide](https://prometheus.io/docs/prometheus/latest/querying/basics/).

Data is kept for `prometheus_retention_time` (default `90d`) or until it reaches
`prometheus_retention_size` (default `50GB`), whichever comes first. Config and rule changes
are checked with `promtool` before they are written, then applied with a live reload.

## Alertmanager

Prometheus evaluates the alert rules in `roles/prometheus/files/rules/`, grouped by area:
targets (a host is down only when none of its exporters answer; one silent exporter is
an exporter alert), host (disk, inodes, memory, clock, failed or unresponsive systemd,
ECC, RAID, NIC errors), GPU (XID, missing GPUs, row remapping, temperature, PCIe replays),
power (PSU, BMC sensors, and chassis intrusion as a warning, since it is a latched event cleared in the BMC) and the monitoring stack itself. Add site rules with
`prometheus_extra_rule_files`. Check rule state at http://\<slurm-metric ip\>:9090/alerts.
`tests/monitoring/alert-rules.test.yml` unit-tests the rules with `promtool test rules`.

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
`slurm_usage_report_dashboard_url`); the thread lists idle jobs, drain/down nodes with
reasons, pending jobs per user, and every running GPU job per user as a table with a
utilisation bar and GPU memory.

A job counts as idle when it has run for `slurm_usage_report_idle_hours` and every GPU it
holds stayed at or under `slurm_usage_report_idle_util_threshold` percent the whole time
(DCGM `DCGM_FI_DEV_GPU_UTIL`). `slurm_usage_report_notify` lists how its owner hears about
it: `mention` in the report thread, `dm`, both, or `[]` for neither. Owners are notified
when a job first turns idle and then every `slurm_usage_report_notify_repeat_hours`, not on
every report; the thread lists idle jobs either way.
`slurm_usage_report_cancel_after_hours` cancels idle jobs after that long (0 = never) and
says so in the report thread.

The role needs a Slack app bot token (`slack_bot_token`, scopes `chat:write`, `im:write`,
`users:read`, `users:read.email`) invited to the channel. Slurm users map to Slack
members through `slurm_usage_report_user_map`, or by email with
`slurm_usage_report_email_domain`. Preview a report without notifying anyone, either
printed or posted to one person's DM (or any channel):

```bash
sudo /usr/local/libexec/slurm-usage-report --dry-run
sudo /usr/local/libexec/slurm-usage-report --to U0123ABCD
```
