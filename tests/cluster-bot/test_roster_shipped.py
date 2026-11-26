"""roles/cluster_bot installs the roster module (plugins/filter/people.py) into the bot's
package with every in-tree module it imports, and the set runs there as it is: the
bot's own environment, no Ansible. 2026-11-26: people.py came to import days.py, the role
shipped people.py alone, and the bot failed to start on every restart."""

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import bounds  # noqa: E402
from ansible_yaml import role_vars  # noqa: E402

FILTERS = ROOT / "plugins" / "filter"
SHIPPED = role_vars("cluster_bot")["_cluster_bot_roster_modules"]


def sibling_imports(path):
    """The plugins/filter modules PATH imports, at any depth of the file."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return {n for n in names if (FILTERS / f"{n}.py").is_file()}


class RosterShipped(unittest.TestCase):
    def test_every_module_it_imports_is_shipped_under_its_own_name(self):
        sources = set(SHIPPED.values())
        for source in sorted(sources):
            for name in sibling_imports(FILTERS / source):
                with self.subTest(source=source, imports=name):
                    self.assertIn(f"{name}.py", sources)
                    self.assertEqual(SHIPPED.get(f"{name}.py"), f"{name}.py", "imported by its file name")

    def test_the_shipped_set_runs_without_ansible(self):
        with tempfile.TemporaryDirectory() as lib:
            package = Path(lib) / "cluster_bot"
            package.mkdir()
            (package / "__init__.py").write_text("")
            for dest, source in SHIPPED.items():
                shutil.copy(FILTERS / source, package / dest)
            probe = (
                "from cluster_bot import people_roster as p\n"
                "import datetime\n"
                "assert p._date(datetime.date(2027, 2, 28)).isoformat() == '2027-02-28'\n"
                "assert p._date('2027-02-28') == datetime.date(2027, 2, 28)\n"
                "assert p.people_login_expires({'ends': '2027-02-28'}, 30) == '2027-03-30'\n"
                "print('ok')\n"
            )
            env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": lib}
            # -S: no site-packages, so no Ansible, as in the bot's environment.
            done = subprocess.run([sys.executable, "-S", "-c", probe], cwd=lib, env=env,
                                  capture_output=True, text=True, timeout=bounds.CHILD)  # fmt: skip
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(done.stdout.strip(), "ok")


class OnlyThroughAList(unittest.TestCase):
    def test_no_role_ships_a_plugin_file_but_through_this_list(self):
        from repo_scan import ansible_files, module, tasks

        found = sorted(
            {
                str(path.relative_to(ROOT))
                for path in ansible_files()
                for task in tasks(path)
                if "plugins/" in str(module(task)[1].get("src", ""))
            }
        )
        self.assertEqual(found, ["roles/cluster_bot/tasks/present.yml"], "ship a plugin with what it imports")


if __name__ == "__main__":
    unittest.main()
