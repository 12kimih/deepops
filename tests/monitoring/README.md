# Monitoring checks

`test_alert_rules.py` runs `alert-rules.test.yml` through `promtool test rules`: synthetic
series against the shipped alert rules in `roles/prometheus/files/rules/`. Add a case with
every new rule, and check each metric's meaning against the exporter's `# HELP` text (the
IPMI chassis fault states, for one, use 1 for "no fault").

Run without a cluster:

```sh
cd tests/monitoring && python3 -m unittest discover -s . -v
```

promtool comes from `$PROMTOOL`, else `promtool` on PATH, else the Prometheus image the
role deploys (`prometheus_container`) through docker (`promtool_runner.py`), so the
checks use the deployed version. Without promtool or docker the tests are skipped.

## GPU Nodes dashboard checks

The checks load `src/dashboards/gpu-dashboard.json`, check every exporter
selector, and execute the variable queries and all panel expressions using
`promtool test rules`. Fixtures cover Kubernetes exporters at different IPs
(and non-default ports), Slurm hostname inventories with short OS hostnames,
and Slurm IP inventories. An unrelated node checks selection isolation.
No metrics server, Grafana instance, GPU, or cluster is needed.

On Kubernetes, the visible server variable uses the `kubernetes_node` label
provided by DeepOps's GPU scrape configuration. Hidden dependent variables
resolve GPU and node-exporter instances independently; node-exporter identity
comes from `node_uname_info.nodename`. On Slurm, GPU metrics without that label
keep the inventory-address fallback and default exporter ports.

These are static/query checks, not live Grafana confirmation. Existing Grafana
imports or Kubernetes dashboard ConfigMaps must be updated to use the new JSON;
the monitoring install script does not replace an existing dashboard ConfigMap.
