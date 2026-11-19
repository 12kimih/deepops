"""Unit tests for validate_monitoring.py against a stand-in Prometheus (no network)."""

import importlib.util
import os
import unittest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("vm", os.path.join(SCRIPTS_DIR, "validate_monitoring.py"))
vm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vm)


class FakePrometheus:
    def __init__(self, names, queries=None, targets=None, rules=None):
        self.names, self.queries = names, queries or {}
        self.targets = targets if targets is not None else [{"health": "up", "labels": {}}]
        self.rules = rules if rules is not None else [{"name": "g", "rules": [{"health": "ok"}]}]

    def get(self, path, **params):
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
