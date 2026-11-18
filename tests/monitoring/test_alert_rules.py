"""Run the alert rule unit tests (alert-rules.test.yml) with promtool."""

import unittest
from pathlib import Path

import yaml

import promtool_runner

HERE = Path(__file__).resolve().parent


class AlertRuleTests(unittest.TestCase):
    def test_alert_rules(self):
        result = promtool_runner.run(
            ["test", "rules", "alert-rules.test.yml"],
            cwd=HERE,
            mounts=[promtool_runner.ROOT / "roles/prometheus/files/rules"],
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_alert_has_a_firing_case(self):
        rules = promtool_runner.ROOT / "roles/prometheus/files/rules"
        alerts = {
            rule["alert"]
            for path in sorted(rules.glob("*.yml"))
            for group in yaml.safe_load(path.read_text())["groups"]
            for rule in group["rules"]
            if "alert" in rule
        }
        cases = yaml.safe_load((HERE / "alert-rules.test.yml").read_text())["tests"]
        tested = {
            check["alertname"]
            for case in cases
            for check in case.get("alert_rule_test", [])
            if check.get("exp_alerts")
        }
        self.assertEqual(sorted(alerts - tested), [], "alert rules without a firing test case")


if __name__ == "__main__":
    unittest.main()
