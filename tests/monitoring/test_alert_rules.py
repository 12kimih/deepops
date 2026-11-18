#!/usr/bin/env python3
"""Run the alert rule unit tests (alert-rules.test.yml) with promtool."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

HERE = Path(__file__).resolve().parent


class AlertRuleTests(unittest.TestCase):
    def test_alert_rules(self):
        tool = shutil.which(os.environ.get("PROMTOOL", "promtool"))
        self.assertIsNotNone(tool, "Install promtool or set PROMTOOL to its path")
        result = subprocess.run([tool, "test", "rules", "alert-rules.test.yml"],
                                cwd=HERE, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
