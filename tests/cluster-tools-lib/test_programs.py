"""Every program in cluster_tools against the library as it is now.

The programs ship in the same package as the library, so a deploy can never mix them;
this test is the matching guard in CI: a library change that removes or renames what a
program imports fails here, not on a host.
"""

import importlib
import pkgutil
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
import cluster_tools  # noqa: E402

PROGRAMS = {
    "sq",
    "qos",
    "gpu_usage",
    "usage_report",
    "metrics",
    "slurm_backup",
    "egress_metrics",
    "container_gc",
    "submit_holds",
}


class ProgramTests(unittest.TestCase):
    def test_every_module_imports(self):
        names = {m.name for m in pkgutil.iter_modules(cluster_tools.__path__)}
        self.assertTrue(PROGRAMS <= names, PROGRAMS - names)
        for name in sorted(names):
            with self.subTest(module=name):
                importlib.import_module(f"cluster_tools.{name}")

    def test_every_program_has_a_main_and_runs_as_a_module(self):
        for name in sorted(PROGRAMS | {"banner"}):
            with self.subTest(program=name):
                module = importlib.import_module(f"cluster_tools.{name}")
                self.assertTrue(callable(module.main))
                self.assertIn('if __name__ == "__main__":', Path(module.__file__).read_text())

    def test_every_built_in_command_is_the_module_it_publishes(self):
        # roles/cluster_tools publishes NAME as cluster_tools.<NAME, - as _>.
        defaults = yaml.safe_load((ROOT / "roles/cluster_tools/defaults/main.yml").read_text())
        for name in defaults["cluster_tools_builtin"]:
            with self.subTest(command=name):
                module = importlib.import_module(f"cluster_tools.{name.replace('-', '_')}")
                self.assertEqual(module.COMMAND.name, name)

    def test_modules_are_imported_never_run_as_files(self):
        # Commands reach a module through its wrapper or `python3 -m`; a shebang or an
        # execute bit invites running the file itself, where package imports break.
        for path in sorted(Path(cluster_tools.__file__).parent.glob("*.py")):
            with self.subTest(module=path.name):
                self.assertFalse(path.read_text().startswith("#!"))
                self.assertFalse(path.stat().st_mode & 0o111, "execute bit set")


if __name__ == "__main__":
    unittest.main()
