"""scripts/deepops/test_units.py: the methods scripts/check.sh runs side by side, and
the whole file whenever a class's tests could not be named."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402
from repo_scan import tracked  # noqa: E402

units = in_tree.load("scripts/deepops/test_units.py").units


class Units(unittest.TestCase):
    def test_methods_of_testcases_direct_and_through_a_local_base(self):
        src = (
            "import unittest\n"
            "class Base(unittest.TestCase):\n    def helper(self): pass\n"
            "class A(Base):\n    def test_one(self): pass\n    def test_two(self): pass\n"
            "class Mixin:\n    def test_not_a_case(self): pass\n"
        )
        self.assertEqual(units(src), ["A.test_one", "A.test_two"])

    def test_an_unseen_base_runs_the_whole_file(self):
        src = "import helpers\nclass A(helpers.Case):\n    def test_one(self): pass\n"
        self.assertEqual(units(src), [])

    def test_the_repositorys_slow_suites_split(self):
        slow = {p.parent for p in tracked("tests/*/.slow")}
        for test_file in tracked("tests/*/test_*.py"):
            if test_file.parent in slow:
                with self.subTest(test_file.name):
                    names = units(test_file.read_text())
                    # every test method of the file is named, or none (whole file)
                    count = test_file.read_text().count("    def test")
                    self.assertIn(len(names), (0, count))


if __name__ == "__main__":
    unittest.main()
