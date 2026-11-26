"""scripts/deepops/check_times.py: scripts/check.sh starts its steps longest first, from
each step's time in the last run, and a step never met before first of all."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402

ct = in_tree.load("scripts/deepops/check_times.py")
SCRIPT = ROOT / "scripts/deepops/check_times.py"


def run(action, path, stdin):
    return subprocess.run([sys.executable, str(SCRIPT), action, str(path)], input=stdin,
                          capture_output=True, text=True, check=True).stdout  # fmt: skip


class Order(unittest.TestCase):
    def test_longest_first_unknown_before_all_ties_as_declared(self):
        names = ["lint", "alert rules", "new step", "style", "docs"]
        times = {"lint": 360, "alert rules": 540, "style": 10, "docs": 10}
        self.assertEqual(ct.order(names, times), [2, 1, 0, 3, 4])

    def test_without_times_the_declared_order(self):
        self.assertEqual(ct.order(["a", "b", "c"], {}), [0, 1, 2])


class Record(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "cache" / "check-times"

    def test_this_runs_times_over_the_others_and_back_in_order(self):
        run("record", self.path, "540\talert rules\n10\tstyle\n")
        run("record", self.path, "12\tstyle\n361\tlint\n")
        self.assertEqual(ct.load(self.path), {"alert rules": 540, "style": 12, "lint": 361})
        self.assertEqual(run("order", self.path, "style\nlint\nalert rules\n"), "2\n1\n0\n")
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["check-times"], "no temporary left")

    def test_a_missing_or_garbled_cache_is_no_times(self):
        self.assertEqual(run("order", self.path, "a\nb\n"), "0\n1\n")
        self.path.parent.mkdir(parents=True)
        self.path.write_text("x\tbroken\nno tab here\n7\tok\n")
        self.assertEqual(ct.load(self.path), {"ok": 7})

    def test_check_sh_starts_steps_in_this_order_and_records_them(self):
        text = (ROOT / "scripts/check.sh").read_text()
        self.assertIn('check_times.py order "${times}"', text)
        self.assertIn('for i in "${order[@]}"; do', text)
        self.assertIn('check_times.py record "${times}"', text)
        self.assertNotIn('for i in "${!names[@]}"; do\n    while', text, "launched in declared order")


if __name__ == "__main__":
    unittest.main()
