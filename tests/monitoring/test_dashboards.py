"""Conventions every shipped Grafana dashboard keeps.

Names follow the monitoring-mixin form "<Domain> / <View>" with a stable lowercase UID,
so dashboards sort by domain and links survive renames. Panels are native ones that draw
a gap as a gap (no legacy graph panel and no "null as zero"). Recording rules a query
uses are defined in the shipped rule files, so a renamed rule cannot leave a panel empty.
"""

import json
import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "roles/prometheus/files/rules"
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402


# Every dashboard deepops provisions, set -> files in src/dashboards/.
SETS = yaml.safe_load((ROOT / "roles/prometheus_paths/defaults/main.yml").read_text())[
    "grafana_dashboard_sets"
]


def dashboards():
    for path in sorted(ROOT / "src/dashboards" / name for names in SETS.values() for name in names):
        yield path.relative_to(ROOT), json.loads(path.read_text())


def panels(items):
    for panel in items:
        yield panel
        yield from panels(panel.get("panels", []))


def all_panels(dash):
    # Schema-16-and-older dashboards keep panels under rows.
    yield from panels(dash.get("panels", []))
    for row in dash.get("rows", []):
        yield from panels(row.get("panels", []))


class DashboardConventionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dashboards = list(dashboards())
        cls.recorded = {
            rule["record"]
            for path in tracked(f"{RULES.relative_to(ROOT)}/*.yml")
            for group in yaml.safe_load(path.read_text())["groups"]
            for rule in group["rules"]
            if "record" in rule
        }

    def test_found(self):
        self.assertGreaterEqual(len(self.dashboards), 4)

    def test_every_dashboard_file_is_provisioned_and_no_set_shares_one(self):
        listed = [name for names in SETS.values() for name in names]
        self.assertEqual(len(listed), len(set(listed)))
        self.assertEqual(sorted(listed), sorted(p.name for p in tracked("src/dashboards/*.json")))
        stray = [
            str(p.relative_to(ROOT))
            for p in tracked("roles/*/files/*.json")
            if "panels" in json.loads(p.read_text())
        ]
        self.assertEqual(stray, [], "dashboards live in src/dashboards/, listed in grafana_dashboard_sets")

    def test_names_and_uids(self):
        seen = {}
        for path, dash in self.dashboards:
            with self.subTest(path=str(path)):
                self.assertRegex(dash["title"], r"^[A-Z][\w ]* / [\w ]+$")
                self.assertNotIn("dashboard", dash["title"].lower())
                self.assertRegex(dash["uid"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
                self.assertIsNone(dash.get("id"))
                self.assertTrue(dash.get("tags"))
                self.assertNotIn(dash["uid"], seen, f"uid also used by {seen.get(dash['uid'])}")
                seen[dash["uid"]] = path
                # The file is named after the UID: Grafana tracks provisioned dashboards
                # by file, so a UID change must come with a new file.
                self.assertEqual(path.stem, dash["uid"])

    def test_gaps_stay_gaps(self):
        for path, dash in self.dashboards:
            for panel in all_panels(dash):
                with self.subTest(path=str(path), panel=panel.get("title")):
                    self.assertNotEqual(panel.get("type"), "graph")
                    self.assertNotEqual(panel.get("nullPointMode"), "null as zero")

    def test_recording_rules_exist(self):
        for path, dash in self.dashboards:
            for panel in all_panels(dash):
                for target in panel.get("targets", []):
                    for name in re.findall(r"\b[a-z_]+:[a-z_:]+\b", target.get("expr", "")):
                        with self.subTest(path=str(path), panel=panel.get("title"), rule=name):
                            self.assertIn(name, self.recorded)


if __name__ == "__main__":
    unittest.main()


class SlurmNativeMetricsTests(unittest.TestCase):
    """The Slurm dashboard reads slurmctld's own metrics: every name must be one the
    pinned Slurm serves (tests/cluster-tools/slurm-names.json, from its source by
    scripts/deepops/slurm_names.py), on an endpoint Prometheus scrapes."""

    @classmethod
    def setUpClass(cls):
        import sys

        sys.path.insert(0, str(ROOT / "scripts/validation"))
        from validate_monitoring import metric_names

        cls.metric_names = staticmethod(metric_names)
        served = json.loads((ROOT / "tests/cluster-tools/slurm-names.json").read_text())["metrics"]
        scraped = yaml.safe_load((ROOT / "roles/prometheus/defaults/main.yml").read_text())[
            "prometheus_slurmctld_metrics"
        ]
        cls.served, cls.scraped = served, scraped

    def test_every_scraped_set_is_an_endpoint(self):
        self.assertEqual(sorted(set(self.scraped) - set(self.served)), [])

    def test_the_slurm_dashboard_reads_only_scraped_native_metrics(self):
        available = {name for s in self.scraped for name in self.served[s]}
        names = set()
        for name in ("slurm-scheduler.json",):
            dash = json.loads((ROOT / "src/dashboards" / name).read_text())
            for panel in panels(dash.get("panels", [])):
                for target in panel.get("targets", []):
                    names |= self.metric_names(target.get("expr", ""))
        self.assertTrue(names)
        self.assertEqual(sorted(names - available), [])
