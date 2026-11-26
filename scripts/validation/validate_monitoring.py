#!/usr/bin/env python3
"""Validate a DeepOps monitoring deployment through Prometheus and report a verdict.

Run it after deploying the monitoring playbooks, from a checkout of this repository on
any host that reaches Prometheus (read-only HTTP API; nothing is changed):

    validate_monitoring.py [--url http://localhost:9090] [--json]

Checks:
  ready        Prometheus answers /-/ready.
  targets      every scrape target is up.
  rules        every rule the repository ships is loaded and every rule group evaluates
               without error (one missing is a rollout gap: deploy prometheus.yml).
  alerting     an Alertmanager is attached and the Watchdog alert reaches it (the
               always-firing alert that proves the alert path end to end).
  metrics      every metric the repository's alert rules and dashboards read exists,
               except those that may be absent (ABSENT_OK, by why). A metric missing while
               others from its exporter exist is a rollout gap: a newer rule or
               dashboard reached Prometheus before the exporter config it needs.
  gpu_join     every GPU DCGM reports joins Slurm's device numbering (the join the
               allocation alerts and dashboard stand on).
  freshness    no staleness alert fires: every timer writing textfile metrics wrote
               lately (the rules' ...Stale and ...NeverReported alerts, whose windows
               are the only ones).

Exit codes: 0 = all checks passed, 1 = a check failed, 2 = Prometheus unreachable.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
sys.path.insert(0, str(ROOT / "scripts/deepops"))
from cluster_tools import metrics as slurm_metrics  # noqa: E402
from cluster_tools.prometheus import Error as PrometheusError  # noqa: E402
from cluster_tools.prometheus import Prometheus  # noqa: E402
from repo_files import tracked  # noqa: E402

RULES = ROOT / "roles" / "prometheus" / "files" / "rules"
# Every dashboard deepops provisions (grafana_dashboard_sets, roles/prometheus_paths).
DASHBOARDS = [
    ROOT / "src" / "dashboards" / name
    for names in yaml.safe_load((ROOT / "roles/prometheus_paths/defaults/main.yml").read_text())[
        "grafana_dashboard_sets"
    ].values()
    for name in names
]
# Metrics whose absence is no failure, by why: the panels reading them stay empty, the
# alerts reading them quiet.
ABSENT_OK = {
    "hardware": (  # only some GPUs, boards, CPUs or fabrics have them
        r"DCGM_FI_DEV_NVLINK_.*|DCGM_FI_PROF_.*|node_infiniband_.*|ipmi_.*|node_power_supply_.*"
        r"|node_hwmon_(?!temp_celsius$|chip_names$|sensor_label$).*|node_cpu_(core|package)_throttles_total"
        r"|DCGM_FI_DEV_MEMORY_(MAX_OP_)?TEMP_CELSIUS"  # HBM GPUs only; GDDR ones report none
    ),
    "kernel": r"node_kernel_hung_tasks_total|node_pressure_irq_.*",  # newer or other kernel builds
    "off by choice": r"node_interrupts_total",  # one series per CPU per IRQ (prometheus-node-exporter)
    "only while in use": (  # a GPU some job holds, an XID or clock event in the exporter's window
        r"slurm_gpu_allocated|DCGM_EXP_XID_ERRORS_COUNT|DCGM_EXP_CLOCK_EVENTS_COUNT|DCGM_FI_DEV_XID_ERROR"
    ),
    # A backup's results; until then its configured time stands in (rules/backup.yml).
    "after the first run": r"slurm_backup_(restore_test_)?(status|last_\w+)",
    # A component's source digest, recorded by its first whole deploy since digests were.
    "after the first whole deploy": r"os_updates_component_source_info",
    "optional component": r".+_tls_cert_not_after_seconds",  # a service that serves TLS (rules/tls.yml)
    # Usage is counted from the start; limits exist once the site sets some, and project
    # series only where it declares projects (roles/disk_quota).
    "only with limits": r"disk_quota_limit_(bytes|inodes)|disk_quota_project_\w+",
    # Folders unused past the stale period: none while every folder is in use.
    "only when something is due": r"disk_cleanup_candidate_\w+",
    "Prometheus' own": r"ALERTS|ALERTS_FOR_STATE",
}


def absent_ok(metric):
    """Why METRIC may be absent (an ABSENT_OK key), or None."""
    return next((why for why, pattern in ABSENT_OK.items() if re.fullmatch(pattern, metric)), None)


# Where each metric comes from, first full match wins: a missing metric is a rollout gap
# only when its source reports others (a component switched off reports none). Every
# metric the rules and dashboards read has one (tests), so a new writer cannot go
# unchecked. Three write slurm_ names: the GPU allocation textfile (its names, from the
# writer itself), the backups, and slurmctld.
SLURM_METRICS = set(re.findall(r"^# TYPE (\S+) ", slurm_metrics.textfile({"jobs": []}, {"nodes": []}), re.M))
SOURCES = {
    "slurm_metrics": "|".join(map(re.escape, sorted(SLURM_METRICS))),
    "slurm_backup": r"slurm_backup_\w+",
    "slurmctld": r"slurm_\w+",
    "DCGM": r"DCGM_\w+",
    "node": r"node_\w+",
    "ipmi": r"ipmi_\w+",
    "os_updates": r"os_updates_\w+",
    "container_storage_gc": r"container_storage_gc_\w+",
    "egress_policy": r"egress_policy_\w+",
    "disk_quota": r"disk_quota_\w+",
    "scratch_space": r"scratch_space_\w+",  # the scratch cleanup (roles/scratch_space)
    # Folders worth cleaning (cluster_tools.cleanup), from disk_quota's scan and the scratch cleanup.
    "disk_cleanup": r"disk_cleanup_\w+",
    # Each host's expected component versions and sources, the textfile written at each
    # prometheus.yml run and sync.py move (scripts/deepops/component_expected.py).
    "component_expected": r"deepops:component_(pin|source):info|deepops_component_expected_\w+",
    # Each watched NFS mount's latency threshold, the textfile each prometheus.yml run
    # writes (prometheus_nfs_client_slow_ms, rules io-usage.yml).
    "nfs_client_slow": r"deepops_nfs_client_slow_seconds",
    "cluster_bot": r"cluster_bot_\w+",  # the bot token's Slack scopes (roles/cluster_bot, cluster_bot.scopes)
    "shared_data": r"shared_data_\w+",  # the shared data's export, the bot's rounds (roles/shared_space)
    "submit_holds": r"submit_holds_\w+",  # who may not submit (roles/submit_holds)
    "gpu_diag_idle": r"gpu_diag_idle_\w+",  # the weekly GPU diagnostics (roles/gpu_diag_idle)
    "io_usage": r"io_usage_\w+",  # each job's and login's I/O (roles/io_usage)
    "io_fairness": r"io_fairness_\w+",  # whether iocost shares a disk (roles/io_fairness)
    "power_policy": r"power_policy_\w+",  # the hosts' power settings held at performance (roles/power_policy)
    "nfs": r"nfs_export_\w+",  # what roles/nfs exports (roles/textfile_keep)
    "nfs_client": r"nfs_client_\w+",  # the mounts the retry could not make (roles/nfs_client)
    "people": r"people_purge_\w+",  # when a leaver's files fall due (roles/people)
    "tls": r"\w+_tls_cert_\w+",  # each service's certificate (rules/tls.yml)
    "prometheus": r"prometheus_\w+|up|scrape_\w+|ALERTS|ALERTS_FOR_STATE",
    "alertmanager": r"alertmanager_\w+",
    "process": r"process_\w+",  # every exporter's own process
}


def source(metric):
    """The SOURCES name METRIC comes from, or None."""
    return next((name for name, pattern in SOURCES.items() if re.fullmatch(pattern, metric)), None)


# How recent a sample must be for its metric to count as present (seconds; many scrape
# intervals).
PRESENT_WINDOW = 300
# The alerts saying a textfile writer stopped or never reported (rules/*.yml).
FRESHNESS_ALERT = r"\w+(Stale|NeverReported)"
PROMQL_WORDS = {
    "by", "on", "and", "or", "unless", "without", "ignoring", "group_left", "group_right",
    "offset", "bool", "time", "vector", "scalar", "inf", "nan", "le", "count", "sum", "max",
    "min", "avg", "topk", "bottomk", "quantile", "stddev", "rate", "irate", "increase",
    "changes", "delta", "deriv", "resets", "label_replace", "label_join", "abs", "absent",
    "absent_over_time", "clamp_min", "clamp_max", "round", "ceil", "floor", "histogram_quantile",
    "max_over_time", "min_over_time", "avg_over_time", "sum_over_time", "count_over_time",
    "last_over_time", "present_over_time", "timestamp", "sort", "sort_desc", "query_result",
    "label_values", "humanizeDuration", "humanize",
}  # fmt: skip


def metric_names(expr):
    """The metric names an expression reads (selectors, label lists, windows, strings
    and dashboard variables aside)."""
    text = re.sub(r'\{[^}]*\}|\[[^]]*\]|"[^"]*"|\$\w+|\$\{[^}]*\}', " ", expr)
    # label lists: by (host), on (instance), group_left (state)
    text = re.sub(r"\b(by|on|without|ignoring|group_left|group_right)\s*\([^)]*\)", " ", text)
    names = set()
    for m in re.finditer(r"[A-Za-z_:][A-Za-z0-9_:]*", text):
        name, rest = m.group(0), text[m.end() :].lstrip()
        if name in PROMQL_WORDS or rest.startswith("(") or re.fullmatch(r"\d.*", name):
            continue
        names.add(name)
    return names


def repo_rules():
    """(file name, rule) for every alert and recording rule the repository ships."""
    for path in tracked(f"{RULES.relative_to(ROOT)}/*.yml"):
        for group in yaml.safe_load(path.read_text())["groups"]:
            for rule in group["rules"]:
                yield path.name, rule


def freshness_alerts():
    """The names of the repository's staleness alerts (FRESHNESS_ALERT)."""
    return sorted(r["alert"] for _, r in repo_rules() if re.fullmatch(FRESHNESS_ALERT, r.get("alert", "")))


def repo_metrics():
    """{metric: [where it is read]} over the alert rules and dashboards, recording rules
    expanded to what they read."""
    reads, recorded = {}, {}
    for file, rule in repo_rules():
        if "record" in rule:
            recorded[rule["record"]] = metric_names(rule["expr"])
        for name in metric_names(rule["expr"]):
            reads.setdefault(name, []).append(f"{file}:{rule.get('alert') or rule.get('record')}")
    for path in DASHBOARDS:
        dash = json.loads(path.read_text())
        panels = [p for top in dash.get("panels", []) for p in [top, *top.get("panels", [])]]
        for panel in panels:
            for target in panel.get("targets", []):
                for name in metric_names(target.get("expr", "")):
                    reads.setdefault(name, []).append(f"{path.name}:{panel.get('title')}")
    return reads, recorded


def check(prom):
    results = {}

    def record(name, ok, detail):
        results[name] = {"ok": ok, "detail": detail}

    targets = prom.get("/api/v1/targets", state="active")["data"]["activeTargets"]
    down = sorted(
        f"{t['labels'].get('job')}/{t['labels'].get('instance')}" for t in targets if t["health"] != "up"
    )
    record("targets", not down and bool(targets), {"total": len(targets), "down": down})

    groups = prom.get("/api/v1/rules")["data"]["groups"]
    broken = [
        f"{g['name']}/{r.get('name')}: {r.get('lastError')}"
        for g in groups
        for r in g["rules"]
        if r.get("health") not in ("ok", None)
    ]
    loaded = {r.get("name") for g in groups for r in g["rules"]}
    unloaded = sorted({r.get("alert") or r.get("record") for _, r in repo_rules()} - loaded)
    record(
        "rules",
        not broken and not unloaded and bool(groups),
        {"groups": len(groups), "errors": broken, "not_loaded": unloaded},
    )

    managers = prom.get("/api/v1/alertmanagers")["data"]["activeAlertmanagers"]
    watchdog = prom.query('ALERTS{alertname="Watchdog", alertstate="firing"}')
    record(
        "alerting",
        bool(managers) and bool(watchdog),
        {"alertmanagers": len(managers), "watchdog_firing": bool(watchdog)},
    )

    # Names with samples now: without a start the API lists every name in the retained
    # data, so a metric gone since (a renamed field, a broken exporter) would count.
    present = set(prom.get("/api/v1/label/__name__/values", start=time.time() - PRESENT_WINDOW)["data"])
    reads, recorded = repo_metrics()
    gaps, allowed = {}, {}
    reporting_sources = {source(m) for m in present}
    for name, where in sorted(reads.items()):
        for metric in sorted(recorded.get(name, {name})):
            if metric in present or metric in recorded:
                continue
            why = absent_ok(metric)
            if why:
                allowed.setdefault(why, set()).add(metric)
                continue
            if source(metric) and source(metric) in reporting_sources:
                gaps.setdefault(metric, sorted(set(where)))
    detail = {"read": len(reads), "missing": gaps, "absent_ok": {w: sorted(m) for w, m in allowed.items()}}
    record("metrics", not gaps, detail)

    reporting = {r["metric"].get("host") for r in prom.query("count by (host) (DCGM_FI_DEV_GPU_UTIL_RATIO)")}
    joined = {
        r["metric"].get("host") for r in prom.query("count by (host) (host_gpu:dcgm_slurm_device:info)")
    }
    record(
        "gpu_join", reporting <= joined, {"gpu_hosts": len(reporting), "unjoined": sorted(reporting - joined)}
    )

    alerts = freshness_alerts()
    firing = prom.query(f'ALERTS{{alertstate="firing", alertname=~"{"|".join(alerts)}"}}')
    stale = sorted(f"{r['metric'].get('alertname')}{{host={r['metric'].get('host')}}}" for r in firing)
    record("freshness", not stale, {"alerts": len(alerts), "firing": stale})
    return results


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--url", default="http://localhost:9090", help="Prometheus base URL")
    p.add_argument("--json", action="store_true", help="print one JSON object")
    args = p.parse_args(argv)
    prom = Prometheus(args.url)
    try:
        ready = "Ready" in str(prom.get("/-/ready"))
        results = {"ready": {"ok": ready, "detail": {}}, **check(prom)}
    except (PrometheusError, KeyError) as e:
        error = str(e) if isinstance(e, PrometheusError) else f"{args.url}: no {e} in a reply"
        out = {"ok": False, "error": error}
        print(json.dumps(out) if args.json else out["error"], file=sys.stdout if args.json else sys.stderr)
        return 2
    ok = all(r["ok"] for r in results.values())
    if args.json:
        print(json.dumps({"ok": ok, "checks": results}, indent=2))
    else:
        for name, r in results.items():
            print(f"{'ok  ' if r['ok'] else 'FAIL'} {name}: {json.dumps(r['detail'])}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
