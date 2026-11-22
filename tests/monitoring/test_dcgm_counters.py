"""Check the dcgm-exporter counters file: a malformed row stops the exporter, and a field
the alerts, dashboards or usage report read must be one it exports."""

import csv
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COUNTERS = ROOT / "roles/nvidia-dcgm-exporter/files/dcgm-custom-metrics.csv"
TYPES = {"gauge", "counter", "label"}
# Code that names DCGM metrics outside PromQL the validator can parse.
READERS = [ROOT / "roles/cluster_tools_lib/files/cluster_tools/usage_report.py"]


def exported():
    with COUNTERS.open() as f:
        return {row[0].strip() for row in csv.reader(f) if row and not row[0].lstrip().startswith("#")}


def validator():
    """scripts/validation/validate_monitoring.py: its metric_names() and repo_metrics()
    are the one reading of what the rules and dashboards query."""
    spec = importlib.util.spec_from_file_location("vm", ROOT / "scripts/validation/validate_monitoring.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DcgmCountersTests(unittest.TestCase):
    def test_rows(self):
        seen = set()
        with COUNTERS.open() as f:
            for line, row in enumerate(csv.reader(f), 1):
                if not row or row[0].lstrip().startswith("#"):
                    continue
                self.assertEqual(len(row), 3, f"line {line}: field, type, help (no commas in help)")
                field, kind = row[0].strip(), row[1].strip()
                self.assertRegex(field, r"^DCGM_(FI|EXP)_[A-Z0-9_]+$", f"line {line}")
                self.assertIn(kind, TYPES, f"line {line}")
                self.assertNotIn(field, seen, f"line {line}: duplicate")
                seen.add(field)

    def test_every_field_read_is_exported(self):
        # The exporter's 4.9 rename left rules and dashboards on names it no longer wrote:
        # whatever reads a DCGM metric must find it in this file.
        reads, recorded = validator().repo_metrics()
        names = {n for n in reads if n.startswith("DCGM_")}
        names |= {n for inputs in recorded.values() for n in inputs if n.startswith("DCGM_")}
        for path in READERS:
            names |= set(re.findall(r"\bDCGM_(?:FI|EXP)_[A-Z0-9_]+", path.read_text()))
        self.assertGreater(len(names), 10)
        self.assertEqual(
            sorted(names - exported()), [], "enable the field in the counters file, or stop reading it"
        )


if __name__ == "__main__":
    unittest.main()
