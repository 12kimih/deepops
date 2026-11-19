"""Run the alert rule unit tests (alert-rules*.test.yml) with promtool."""

import re
import unittest
from pathlib import Path

import yaml

import promtool_runner

HERE = Path(__file__).resolve().parent
# One file per evaluation interval: promtool evaluates every rule at the file's
# interval over each case's whole span.
CASE_FILES = sorted(p.name for p in HERE.glob("alert-rules*.test.yml"))


class AlertRuleTests(unittest.TestCase):
    def test_alert_rules(self):
        result = promtool_runner.run(
            ["test", "rules", *CASE_FILES],
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
        cases = [case for name in CASE_FILES for case in yaml.safe_load((HERE / name).read_text())["tests"]]
        tested = {
            check["alertname"]
            for case in cases
            for check in case.get("alert_rule_test", [])
            if check.get("exp_alerts")
        }
        self.assertEqual(sorted(alerts - tested), [], "alert rules without a firing test case")

    def test_no_two_alerts_watch_the_same_condition(self):
        # Two sessions once each added "the hosts run different kernels": every mismatch
        # then paged twice. An alert is its metrics (with their selectors), its thresholds
        # and its severity; two alerts sharing all three are one condition.
        rules = promtool_runner.ROOT / "roles/prometheus/files/rules"
        functions = {"by", "on", "and", "or", "unless", "without", "ignoring", "group_left", "group_right",
                     "offset", "bool", "time", "vector", "scalar", "inf", "nan", "count", "sum", "max", "min",
                     "avg", "rate", "increase", "changes", "label_replace", "abs", "absent", "query"}  # fmt: skip
        seen = {}
        for path in sorted(rules.glob("*.yml")):
            for group in yaml.safe_load(path.read_text())["groups"]:
                for rule in group["rules"]:
                    if "alert" not in rule:
                        continue
                    expr = re.sub(r"\[[^]]*\]|\s+", "", rule["expr"])  # windows and spacing aside
                    # each metric with its selector: two label filters are two conditions
                    metrics = {
                        m.group(0)
                        for m in re.finditer(r"([A-Za-z_:][A-Za-z0-9_:]*)(\{[^}]*\})?", expr)
                        if m.group(1) not in functions
                        and not m.group(1).startswith("__")
                        and not expr[m.end() :].startswith("(")
                    }
                    numbers = re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", re.sub(r"\{[^}]*\}", "", expr))
                    severity = rule.get("labels", {}).get("severity")
                    key = (frozenset(metrics), tuple(sorted(numbers)), severity)
                    seen.setdefault(key, []).append(f"{path.name}:{rule['alert']}")
        self.assertEqual([names for names in seen.values() if len(names) > 1], [])


if __name__ == "__main__":
    unittest.main()
