"""Every program in clustertools against the library as it is now.

The programs ship in the same package as the library, so a deploy can never mix them;
this test is the matching guard in CI: a library change that removes or renames what a
program imports fails here, not on a host.
"""

import importlib
import pkgutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
import clustertools  # noqa: E402

PROGRAMS = {"sq", "qos", "usage_report", "metrics"}


class ProgramTests(unittest.TestCase):
    def test_every_module_imports(self):
        names = {m.name for m in pkgutil.iter_modules(clustertools.__path__)}
        self.assertTrue(PROGRAMS <= names, PROGRAMS - names)
        for name in sorted(names):
            with self.subTest(module=name):
                importlib.import_module(f"clustertools.{name}")

    def test_every_program_has_a_main(self):
        for name in sorted(PROGRAMS):
            with self.subTest(program=name):
                self.assertTrue(callable(importlib.import_module(f"clustertools.{name}").main))


if __name__ == "__main__":
    unittest.main()
