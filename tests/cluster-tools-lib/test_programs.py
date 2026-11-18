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

    def test_every_program_has_a_main_and_runs_as_a_module(self):
        for name in sorted(PROGRAMS | {"banner"}):
            with self.subTest(program=name):
                module = importlib.import_module(f"clustertools.{name}")
                self.assertTrue(callable(module.main))
                self.assertIn('if __name__ == "__main__":', Path(module.__file__).read_text())

    def test_modules_are_imported_never_run_as_files(self):
        # Commands reach a module through its wrapper or `python3 -m`; a shebang or an
        # execute bit invites running the file itself, where package imports break.
        for path in sorted(Path(clustertools.__file__).parent.glob("*.py")):
            with self.subTest(module=path.name):
                self.assertFalse(path.read_text().startswith("#!"))
                self.assertFalse(path.stat().st_mode & 0o111, "execute bit set")


if __name__ == "__main__":
    unittest.main()
