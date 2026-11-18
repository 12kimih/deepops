"""Check the dcgm-exporter counters file: a malformed row stops the exporter."""

import csv
import unittest
from pathlib import Path

COUNTERS = Path(__file__).resolve().parents[2] / "roles/nvidia-dcgm-exporter/files/dcgm-custom-metrics.csv"
TYPES = {"gauge", "counter", "label"}


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


if __name__ == "__main__":
    unittest.main()
