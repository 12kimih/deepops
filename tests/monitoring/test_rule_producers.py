"""Every series an alert or recording rule reads has a producer in the repository, with
the labels the rule selects on. A rule reading a name nothing writes is blind and never
fires, and so is one matching a label its producer does not set: renaming a metric or a
label in one place and not the other passed every test (NfsExportFilling, ContainerOutside
Slurm, 2026-11-23 audit).

A name counts as produced when it is the output of a recording rule, a metric the
pinned Slurm serves on an endpoint Prometheus scrapes (tests/cluster-tools/slurm-names.json),
one of the exporters' own families (node_exporter, ipmi-exporter, Prometheus,
Alertmanager; DCGM's fields are held to the exporter's counters file by
test_dcgm_counters), or a word some producer writes: a role's files, templates or tasks,
the cluster tools, the scripts. For such a written metric, each label a matcher selects
on must appear in a producer of it (scrape labels aside). Whether a component that
produces a series is deployed is the hosts' business: the staleness alerts watch that."""

import json
import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "validation"))
from validate_monitoring import metric_names, repo_rules  # noqa: E402

sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

EXPORTER_FAMILIES = ("node_", "ipmi_", "prometheus_", "alertmanager_", "DCGM_")
BUILTIN = {"up", "ALERTS", "ALERTS_FOR_STATE"}
SCRAPE_LABELS = {"host", "instance", "job", "__name__"}
SELECTOR = re.compile(r"([A-Za-z_:][A-Za-z0-9_:]*)\{([^}]*)\}")
MATCHER = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:=~|!~|!=|=)")


def producer_sources():
    """{path: text} of everything that may write a series: tracked files under roles/
    and scripts/, the rules themselves aside."""
    out = {}
    for path in tracked("roles", "scripts"):
        name = str(path.relative_to(ROOT))
        if "/files/rules/" in name:
            continue
        try:
            out[name] = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
    return out


def native_slurm():
    served = json.loads((ROOT / "tests/cluster-tools/slurm-names.json").read_text())["metrics"]
    scraped = yaml.safe_load((ROOT / "roles/prometheus/defaults/main.yml").read_text())[
        "prometheus_slurmctld_metrics"
    ]
    return {name for endpoint in scraped for name in served[endpoint]}


def outside(name, records, native):
    """Is NAME produced by something other than a written producer?"""
    return name in records or name in native or name in BUILTIN or name.startswith(EXPORTER_FAMILIES)


def unproduced(rules, sources, native):
    """rule: name for each name a rule reads that nothing produces."""
    records = {rule["record"] for _, rule in rules if "record" in rule}
    found = []
    for path, rule in rules:
        for name in sorted(metric_names(rule["expr"])):
            if outside(name, records, native):
                continue
            if not any(re.search(rf"\b{re.escape(name)}\b", text) for text in sources.values()):
                found.append(f"{path}: {rule.get('alert') or rule.get('record')}: {name}")
    return found


def unwritten_labels(rules, sources, native):
    """rule: name{label} for each label a matcher selects on that no producer of the name sets."""
    records = {rule["record"] for _, rule in rules if "record" in rule}
    found = []
    for path, rule in rules:
        for match in SELECTOR.finditer(rule["expr"]):
            name, body = match[1], match[2]
            if outside(name, records, native):
                continue
            producers = [text for text in sources.values() if re.search(rf"\b{re.escape(name)}\b", text)]
            for label in MATCHER.findall(body):
                if label in SCRAPE_LABELS:
                    continue
                written = re.compile(rf"""\b{label}\b\s*[=:]|["']{label}["']""")
                if not any(written.search(text) for text in producers):
                    found.append(f"{path}: {rule.get('alert') or rule.get('record')}: {name}{{{label}}}")
    return found


class RuleProducers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = list(repo_rules())
        cls.sources = producer_sources()
        cls.native = native_slurm()

    def test_every_series_a_rule_reads_has_a_producer(self):
        self.assertEqual(unproduced(self.rules, self.sources, self.native), [])

    def test_every_label_a_rule_selects_on_is_written(self):
        self.assertEqual(unwritten_labels(self.rules, self.sources, self.native), [])

    def test_the_checks_catch_a_rename(self):
        rules = [("x.yml", {"alert": "A", "expr": 'thing_seconds{kind="a"} > 0'}),
                 ("x.yml", {"alert": "B", "expr": "node_load1 > 1 and up == 0"})]  # fmt: skip
        sources = {"writer.py": 'series("thing_seconds", {"sort": 1})'}
        self.assertEqual(unproduced(rules, {}, set()), ["x.yml: A: thing_seconds"])
        self.assertEqual(unproduced(rules, sources, set()), [])
        self.assertEqual(unwritten_labels(rules, sources, set()), ["x.yml: A: thing_seconds{kind}"])
        self.assertEqual(unwritten_labels(rules, {"w.py": 'series("thing_seconds", {"kind": 1})'}, set()), [])
        self.assertEqual(unproduced(rules, {"w": "thing_seconds_total"}, set()), ["x.yml: A: thing_seconds"])


if __name__ == "__main__":
    unittest.main()
