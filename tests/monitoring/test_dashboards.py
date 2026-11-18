"""Conventions every shipped Grafana dashboard keeps.

Names follow the monitoring-mixin form "<Domain> / <View>" with a stable lowercase UID,
so dashboards sort by domain and links survive renames. Panels are native ones that draw
a gap as a gap (no legacy graph panel and no "null as zero"). Recording rules a query
uses are defined in the shipped rule files, so a renamed rule cannot leave a panel empty.
"""

import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "roles/prometheus/files/rules"


def dashboards():
    for path in sorted([*ROOT.glob("src/dashboards/*.json"), *ROOT.glob("roles/*/files/*.json")]):
        data = json.loads(path.read_text())
        if isinstance(data, dict) and ("panels" in data or "rows" in data):
            yield path.relative_to(ROOT), data


def panels(items):
    for panel in items:
        yield panel
        yield from panels(panel.get("panels", []))


def all_panels(dash):
    # Schema-16-and-older dashboards keep panels under rows.
    yield from panels(dash.get("panels", []))
    for row in dash.get("rows", []):
        yield from panels(row.get("panels", []))


# Slurm / Scheduler predates native panels; it is rewritten on Slurm's own metrics with
# the Slurm 26.05 upgrade, which retires its exporter.
LEGACY_PANELS = {"slurm-scheduler"}


class DashboardConventionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dashboards = list(dashboards())
        cls.recorded = {
            rule["record"]
            for path in RULES.glob("*.yml")
            for group in yaml.safe_load(path.read_text())["groups"]
            for rule in group["rules"]
            if "record" in rule
        }

    def test_found(self):
        self.assertGreaterEqual(len(self.dashboards), 4)

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
            if dash["uid"] in LEGACY_PANELS:
                continue
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
