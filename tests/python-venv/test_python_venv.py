"""roles/python_venv: the OS packages a venv needs, and its build script, run for real
on a scratch directory: a finished build is recorded and not repeated, a changed step
builds it again, and a build that stopped partway is built again on the next run (the
cluster bot's first deploy failed for want of python3-venv, 2026-11-25; a venv made but
never filled was then taken as done)."""

import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402
from ansible_yaml import load, render, role_defaults  # noqa: E402

venv = in_tree.load("plugins/filter/python_venv.py")
VARS = load("roles/python_venv/vars/main.yml")
BUILD = next(t for t in load("roles/python_venv/tasks/create.yml") if t["name"].startswith("Build"))


class Packages(unittest.TestCase):
    def test_debian_needs_the_venv_package_of_the_interpreter(self):
        self.assertEqual(venv.python_venv_packages("python3", "Debian"), ["python3-venv"])
        self.assertEqual(venv.python_venv_packages("/usr/bin/python3.12", "Debian"), ["python3.12-venv"])
        self.assertEqual(venv.python_venv_packages("python3", "RedHat"), [])
        self.assertEqual(venv.python_venv_items("python3", "Debian"), ["package:python3-venv"])

    def test_every_venv_in_the_roles_is_made_here(self):
        hits = subprocess.run(["git", "-C", str(ROOT), "grep", "--untracked", "-l", "-e", "-m venv", "--", "roles/*.yml", "roles/*.j2"],
                              capture_output=True, text=True).stdout.split()  # fmt: skip
        self.assertEqual(hits, ["roles/python_venv/tasks/create.yml"])


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name, "venv"))

    def tearDown(self):
        self.tmp.cleanup()

    def values(self, steps):
        return {**role_defaults("python_venv"), **VARS, "python_venv_path": self.path,
                "python_venv_python": sys.executable, "python_venv_steps": steps}  # fmt: skip

    def run_task(self, steps):
        """Run the task as change_shell would: the unless probe first, then the script.
        True when the script ran."""
        v = self.values(steps)
        probe = [render(word, v) for word in BUILD["change_shell"]["unless"]]
        if subprocess.run(probe).returncode == 0:
            return False
        subprocess.run(
            ["bash", "-c", render(BUILD["change_shell"]["cmd"], v)], check=True, capture_output=True
        )
        return True

    def test_built_once_then_kept_and_rebuilt_when_a_step_changes(self):
        steps = [{"requirements": "# nothing pinned\n", "args": ""}]
        self.assertTrue(self.run_task(steps))
        self.assertTrue(Path(self.path, "bin", "python").exists())
        self.assertEqual(Path(self.path, "requirements-1.txt").read_text(), "# nothing pinned\n")
        self.assertFalse(self.run_task(steps))
        self.assertTrue(self.run_task([{"requirements": "# another\n", "args": ""}]))

    def test_a_build_that_stopped_partway_is_built_again(self):
        bad = [{"requirements": "no-such-package-xyz==1.0 --hash=sha256:" + "0" * 64 + "\n",
                "args": "--no-index"}]  # fmt: skip
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_task(bad)
        self.assertTrue(Path(self.path, "bin", "python").exists())  # made, never filled
        self.assertFalse(Path(self.path, ".deepops-venv").exists())
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_task(bad)  # tried again, not taken as done

    def test_requirements_are_written_as_given(self):
        text = "a==1 --hash=sha256:" + "1" * 64 + "\n# it's quoted $HOME `x`\n"
        script = render(BUILD["change_shell"]["cmd"], self.values([{"requirements": text, "args": ""}]))
        self.assertIn(shlex.quote(text.strip()), script)


if __name__ == "__main__":
    unittest.main()
