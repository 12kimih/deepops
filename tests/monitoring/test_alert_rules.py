"""Run the alert rule unit tests (alert-rules*.test.yml) with promtool."""

import re
import tempfile
import unittest
from pathlib import Path

import yaml

import promtool_runner

HERE = Path(__file__).resolve().parent
# One file per evaluation interval: promtool evaluates every rule at the file's
# interval over each case's whole span.
CASE_FILES = sorted(p.name for p in HERE.glob("alert-rules*.test.yml"))
RULES = promtool_runner.ROOT / "roles/prometheus/files/rules"


def eval_order():
    """Every group, those that only record first. Without group_eval_order promtool
    evaluates the groups in random order, and at an hourly evaluation_interval a rule
    reading another group's recorded series sees this step's sample or, past the 5m
    lookback, none. Prometheus itself evaluates each group every minute or less, so
    there the series is always fresh; the order matters to the tests only."""
    groups = [g for path in sorted(RULES.glob("*.yml")) for g in yaml.safe_load(path.read_text())["groups"]]
    records = [g["name"] for g in groups if all("record" in r for r in g["rules"])]
    return records + [g["name"] for g in groups if g["name"] not in records]


class AlertRuleTests(unittest.TestCase):
    def test_alert_rules(self):
        order = eval_order()
        with tempfile.TemporaryDirectory() as tmp:
            for name in CASE_FILES:
                case = yaml.safe_load((HERE / name).read_text())
                case["rule_files"] = [str((HERE / f).resolve()) for f in case["rule_files"]]
                case["group_eval_order"] = order
                Path(tmp, name).write_text(yaml.safe_dump(case, sort_keys=False))
            result = promtool_runner.run(["test", "rules", *CASE_FILES], cwd=tmp, mounts=[RULES])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_recorded_series_are_fresh_where_read(self):
        # Each recording group comes before the groups that read it, with every group
        # named (an unnamed group sorts beside the first named one, in random order).
        order = eval_order()
        groups = [
            g for path in sorted(RULES.glob("*.yml")) for g in yaml.safe_load(path.read_text())["groups"]
        ]
        self.assertEqual(sorted(order), sorted(g["name"] for g in groups))
        self.assertEqual(len(order), len(set(order)), "group names must be unique across rule files")
        self.assertLess(order.index("components"), order.index("os-updates"))

    def test_every_alert_has_a_firing_case(self):
        alerts = {
            rule["alert"]
            for path in sorted(RULES.glob("*.yml"))
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

    def test_power_policy_waits_span_several_runs(self):
        # The rules are static; the timer runs every power_policy_interval. A wait under
        # a few runs would page between two runs that set a switched setting back.
        def seconds(span):
            number, unit = re.fullmatch(r"(\d+)\s*(s|min|m|h)?", str(span)).groups()
            return int(number) * {"h": 3600, "min": 60, "m": 60}.get(unit, 1)

        interval = seconds(
            yaml.safe_load((promtool_runner.ROOT / "roles/power_policy/defaults/main.yml").read_text())[
                "power_policy_interval"
            ]
        )
        rules = {
            r["alert"]: r
            for g in yaml.safe_load((RULES / "power-policy.yml").read_text())["groups"]
            for r in g["rules"]
        }
        self.assertGreaterEqual(seconds(rules["PowerPolicyKeepsBeingReset"]["for"]), 3 * interval)
        stale = int(re.search(r"> (\d+)", rules["PowerPolicyStale"]["expr"]).group(1))
        self.assertGreaterEqual(stale, 3 * interval)

    def test_every_alert_says_what_to_do(self):
        # A summary says what happened; whoever is paged needs the next step too (which
        # unit, which command, on which host). Watchdog only feeds the dead man's switch.
        bare = [
            f"{path.name}: {rule['alert']}"
            for path in sorted(RULES.glob("*.yml"))
            for group in yaml.safe_load(path.read_text())["groups"]
            for rule in group["rules"]
            if "alert" in rule
            and rule["alert"] != "Watchdog"
            and not (rule.get("annotations") or {}).get("description")
        ]
        self.assertEqual(bare, [], "give each alert a description: what to run, where")

    def test_no_two_alerts_watch_the_same_condition(self):
        # Two sessions once each added "the hosts run different kernels": every mismatch
        # then paged twice. An alert is its metrics (with their selectors), its thresholds
        # and its severity; two alerts sharing all three are one condition.
        functions = {"by", "on", "and", "or", "unless", "without", "ignoring", "group_left", "group_right",
                     "offset", "bool", "time", "vector", "scalar", "inf", "nan", "count", "sum", "max", "min",
                     "avg", "rate", "increase", "changes", "label_replace", "abs", "absent", "query"}  # fmt: skip
        seen = {}
        for path in sorted(RULES.glob("*.yml")):
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
