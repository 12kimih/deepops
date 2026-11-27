"""Unit tests for validate_monitoring.py against a stand-in Prometheus (no network)."""

import importlib.util
import os
import unittest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("vm", os.path.join(SCRIPTS_DIR, "validate_monitoring.py"))
vm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vm)


# Every rule the repository ships, loaded and healthy.
LOADED = [
    {
        "name": "g",
        "rules": [
            {"name": n, "health": "ok"}
            for n in {r.get("alert") or r.get("record") for _, r in vm.repo_rules()}
        ],
    }
]


class FakePrometheus:
    def __init__(self, names, queries=None, targets=None, rules=None):
        self.names, self.queries = names, queries or {}
        self.targets = targets if targets is not None else [{"health": "up", "labels": {}}]
        self.rules = rules if rules is not None else LOADED

    def get(self, path, **params):
        self.params = getattr(self, "params", {})
        self.params[path] = params
        return {
            "/api/v1/targets": {"data": {"activeTargets": self.targets}},
            "/api/v1/rules": {"data": {"groups": self.rules}},
            "/api/v1/alertmanagers": {"data": {"activeAlertmanagers": [{}]}},
            "/api/v1/label/__name__/values": {"data": sorted(self.names)},
        }[path]

    def query(self, expr):
        return self.queries.get(expr, [])


WATCHDOG = 'ALERTS{alertname="Watchdog", alertstate="firing"}'


class MetricNames(unittest.TestCase):
    def test_selectors_windows_functions_and_variables_are_not_metrics(self):
        expr = 'sum by (host) (rate(node_cpu_seconds_total{mode!="idle", instance="$node"}[5m])) / on (host) count(up)'
        self.assertEqual(vm.metric_names(expr), {"node_cpu_seconds_total", "up"})

    def test_an_offsets_duration_is_not_a_metric(self):
        expr = "slack_dm_messages_total > 0 unless slack_dm_messages_total offset 1h"
        self.assertEqual(vm.metric_names(expr), {"slack_dm_messages_total"})

    def test_the_repository_reads_metrics(self):
        reads, recorded = vm.repo_metrics()
        self.assertIn("DCGM_FI_DEV_GPU_UTIL_RATIO", reads)
        self.assertIn("host_device:slurm_gpu_allocated:max", recorded)


class Checks(unittest.TestCase):
    def test_a_field_missing_beside_its_exporters_others_is_a_rollout_gap(self):
        names = {"DCGM_FI_DEV_GPU_UTIL_RATIO", "node_uname_info", "up"}
        result = vm.check(FakePrometheus(names, {WATCHDOG: [{}]}))
        self.assertFalse(result["metrics"]["ok"])
        self.assertIn("DCGM_FI_DEV_GPU_MINOR_NUMBER", result["metrics"]["detail"]["missing"])
        self.assertNotIn(
            "DCGM_FI_DEV_NVLINK_THROUGHPUT_BYTES_PER_SECOND", result["metrics"]["detail"]["missing"]
        )
        self.assertNotIn("slurm_gpu_allocated", result["metrics"]["detail"]["missing"])

    def test_a_source_switched_off_is_not_a_gap_beside_another_writing_slurm_names(self):
        # slurmctld, the GPU allocation textfile and the backups all write slurm_ names:
        # one present says nothing about the others (backups or the textfile off).
        names = {"slurm_nodes", "slurm_jobs_running", "up"}
        missing = vm.check(FakePrometheus(names, {WATCHDOG: [{}]}))["metrics"]["detail"]["missing"]
        self.assertNotIn("slurm_backup_destination_configured_timestamp_seconds", missing)
        self.assertNotIn("slurm_metrics_updated_timestamp_seconds", missing)
        self.assertIn("slurm_nodes_down", missing, "slurmctld's own names are one source")
        self.assertEqual(vm.source("slurm_gpu_configured"), "slurm_metrics")
        self.assertEqual(vm.source("slurm_node_gpus"), "slurmctld")
        self.assertEqual(vm.source("slurm_backup_last_status"), "slurm_backup")
        self.assertEqual(vm.source("prometheus_tsdb_compactions_failed_total"), "prometheus")

    def test_every_metric_the_repository_reads_has_a_source(self):
        # A metric without one is never a gap: a new writer must name its source.
        reads, recorded = vm.repo_metrics()
        sourceless = sorted(
            metric
            for name in reads
            for metric in recorded.get(name, {name})
            if metric not in recorded and vm.source(metric) is None
        )
        self.assertEqual(sourceless, [], "add each to validate_monitoring.SOURCES")
        self.assertEqual(vm.source("open_ondemand_tls_cert_not_after_seconds"), "tls")

    def test_metrics_that_may_be_absent_are_listed_by_why_not_missing(self):
        names = {"DCGM_FI_DEV_GPU_UTIL_RATIO", "node_uname_info", "up"}
        detail = vm.check(FakePrometheus(names, {WATCHDOG: [{}]}))["metrics"]["detail"]
        for metric, why in (
            ("node_hwmon_fan_rpm", "hardware"),
            ("DCGM_FI_DEV_MEMORY_TEMP_CELSIUS", "hardware"),
            ("node_pressure_irq_stalled_seconds_total", "kernel"),
            ("node_interrupts_total", "off by choice"),
            ("slurm_backup_restore_test_status", "after the first run"),
        ):
            self.assertNotIn(metric, detail["missing"])
            self.assertIn(metric, detail["absent_ok"][why])
        # The base hwmon series every board has, and the backups' configured times, count.
        self.assertIsNone(vm.absent_ok("node_hwmon_temp_celsius"))
        self.assertIsNone(vm.absent_ok("slurm_backup_restore_test_configured_timestamp_seconds"))

    def test_only_names_sampled_recently_count_as_present(self):
        # Without a start the API lists every name in the retained data, a renamed or
        # vanished metric included.
        prom = FakePrometheus({"up"}, {WATCHDOG: [{}]})
        vm.check(prom)
        start = prom.params["/api/v1/label/__name__/values"]["start"]
        self.assertAlmostEqual(start, vm.time.time() - vm.PRESENT_WINDOW, delta=60)

    def test_down_targets_rule_errors_and_a_silent_watchdog_fail(self):
        prom = FakePrometheus(
            {"up"},
            targets=[{"health": "down", "labels": {"job": "cluster", "instance": "n1:9100"}}],
            rules=[{"name": "g", "rules": [{"name": "r", "health": "err", "lastError": "bad"}]}],
        )
        result = vm.check(prom)
        self.assertEqual(result["targets"]["detail"]["down"], ["cluster/n1:9100"])
        self.assertFalse(result["rules"]["ok"])
        self.assertFalse(result["alerting"]["ok"])

    def test_a_firing_staleness_alert_fails_freshness(self):
        # The rules hold each writer's window; the validator only asks which fire.
        alerts = vm.freshness_alerts()
        self.assertIn("SlurmGpuMetricsStale", alerts)
        self.assertIn("ContainerStorageCleanupNeverReported", alerts)
        self.assertIn("SlurmBackupStale", alerts)
        expr = f'ALERTS{{alertstate="firing", alertname=~"{"|".join(alerts)}"}}'
        firing = [{"metric": {"alertname": "OsUpdatesMetricsStale", "host": "n1"}}]
        result = vm.check(FakePrometheus({"up"}, {WATCHDOG: [{}], expr: firing}))
        self.assertEqual(result["freshness"]["detail"]["firing"], ["OsUpdatesMetricsStale{host=n1}"])
        self.assertFalse(result["freshness"]["ok"])
        self.assertTrue(vm.check(FakePrometheus({"up"}, {WATCHDOG: [{}]}))["freshness"]["ok"])

    def test_a_shipped_rule_prometheus_has_not_loaded_is_a_rollout_gap(self):
        self.assertTrue(vm.check(FakePrometheus({"up"}, {WATCHDOG: [{}]}))["rules"]["ok"])
        rules = [{"name": "g", "rules": [r for r in LOADED[0]["rules"] if r["name"] != "SlurmBackupStale"]}]
        result = vm.check(FakePrometheus({"up"}, {WATCHDOG: [{}]}, rules=rules))
        self.assertFalse(result["rules"]["ok"])
        self.assertEqual(result["rules"]["detail"]["not_loaded"], ["SlurmBackupStale"])

    def test_a_gpu_host_without_the_slurm_join_fails(self):
        queries = {
            WATCHDOG: [{}],
            "count by (host) (DCGM_FI_DEV_GPU_UTIL_RATIO)": [
                {"metric": {"host": "n1"}},
                {"metric": {"host": "n2"}},
            ],
            "count by (host) (host_gpu:dcgm_slurm_device:info)": [{"metric": {"host": "n1"}}],
        }
        result = vm.check(FakePrometheus({"up"}, queries))
        self.assertEqual(result["gpu_join"]["detail"]["unjoined"], ["n2"])


if __name__ == "__main__":
    unittest.main()
