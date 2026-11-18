"""Run the alert rule unit tests (alert-rules.test.yml) with promtool."""
import unittest
from pathlib import Path

import promtool_runner

HERE = Path(__file__).resolve().parent


class AlertRuleTests(unittest.TestCase):
    def test_alert_rules(self):
        result = promtool_runner.run(["test", "rules", "alert-rules.test.yml"], cwd=HERE,
                                     mounts=[promtool_runner.ROOT / "roles/prometheus/files/rules"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
