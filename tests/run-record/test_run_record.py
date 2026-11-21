"""Every Ansible run is recorded with ARA; test runs are not; no_log values never are.

The operator ran a playbook, copied its output and pasted it to whoever reviewed it,
often cut short. ARA (ansible.cfg callback_plugins -> .callback_plugins, linked by
scripts/setup.sh to the package's own directory) keeps each run's tasks, results and
diffs in ~/.ara/server, read with `ara`. scripts/check.sh and scripts/deepops/molecule.py
point ANSIBLE_CALLBACK_PLUGINS at an empty directory, so test playbooks leave no record.
"""

import configparser
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

BIN = Path(sys.executable).parent
SECRET = "run-record-secret-value"
PLAY = f"""
- hosts: localhost
  gather_facts: true
  gather_subset: [min]
  connection: local
  tasks:
    - ansible.builtin.command: echo visible-output
      changed_when: false
    - ansible.builtin.debug:
        msg: "{SECRET}"
      no_log: true
"""


class Wiring(unittest.TestCase):
    def test_ansible_cfg(self):
        cfg = configparser.ConfigParser()
        cfg.read(ROOT / "ansible.cfg")
        self.assertEqual(cfg["defaults"]["callback_plugins"], "./.callback_plugins")

    def test_setup_links_the_package_directory(self):
        setup = (ROOT / "scripts/setup.sh").read_text()
        self.assertIn(
            'ln -sfn "$(uv run --locked python -m ara.setup.callback_plugins)" .callback_plugins', setup
        )
        self.assertIn("/.callback_plugins", (ROOT / ".gitignore").read_text().splitlines())

    def test_tests_record_nothing(self):
        """Every test and scenario that starts Ansible runs it unrecorded, alone or in check.sh."""
        check = (ROOT / "scripts/check.sh").read_text()
        self.assertIn('export ANSIBLE_CALLBACK_PLUGINS="${logs}/no-callbacks"', check)
        self.assertIn("unrecorded.env(", (ROOT / "scripts/deepops/molecule.py").read_text())
        # A program name as an argument (Python) or a command (shell), not a mention in text.
        starts = {
            ".py": re.compile(
                r"[\"']ansible(-playbook)?[\"']|with_name\([\"']ansible|/ \"ansible-playbook\""
            ),
            ".sh": re.compile(r"(?m)^[^#\n]*\bansible(-playbook)? "),
        }
        offenders = []
        for path in tracked("tests/*.py", "tests/*.sh", "scripts/validation/tests/*.py"):
            rel = path.relative_to(ROOT).as_posix()
            text = path.read_text()
            if rel == "tests/run-record/test_run_record.py" or not starts[Path(rel).suffix].search(text):
                continue  # this file sets ANSIBLE_CALLBACK_PLUGINS itself, to test recording
            if "unrecorded." not in text and "export ANSIBLE_CALLBACK_PLUGINS" not in text:
                offenders.append(rel)
        self.assertEqual(offenders, [], "run Ansible with unrecorded.env() (scripts/deepops/unrecorded.py)")


class Recording(unittest.TestCase):
    def run_play(self, base, callbacks):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(d)])
        (d / "play.yml").write_text(PLAY)
        env = {**os.environ, "ARA_BASE_DIR": str(base), "ANSIBLE_CALLBACK_PLUGINS": str(callbacks),
               "ANSIBLE_CONFIG": str(ROOT / "ansible.cfg"), "ANSIBLE_LOCALHOST_WARNING": "false",
               # facts gathered every time, into memory, never the operator's fact cache
               "ANSIBLE_GATHERING": "implicit", "ANSIBLE_CACHE_PLUGIN": "memory"}  # fmt: skip
        out = subprocess.run(
            [str(BIN / "ansible-playbook"), "-i", "localhost,", str(d / "play.yml")],
            env=env, capture_output=True, text=True, timeout=300,
        )  # fmt: skip
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return env

    def ara(self, env, *args):
        out = subprocess.run([str(BIN / "ara"), *args, "-f", "json"], env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_a_run_is_recorded_without_secrets(self):
        callbacks = subprocess.run(
            [sys.executable, "-m", "ara.setup.callback_plugins"], capture_output=True, text=True, check=True
        ).stdout.strip()
        base = Path(tempfile.mkdtemp()) / "ara"
        self.addCleanup(subprocess.run, ["rm", "-rf", str(base.parent)])
        env = self.run_play(base, callbacks)
        playbooks = self.ara(env, "playbook", "list")
        self.assertEqual([p["status"] for p in playbooks], ["completed"])
        content = json.dumps([self.ara(env, "result", "show", str(r["id"]), "--with-content")
                              for r in self.ara(env, "result", "list")])  # fmt: skip
        self.assertIn("visible-output", content)
        self.assertNotIn(SECRET, content)
        self.assertNotIn(SECRET.encode(), (base / "ansible.sqlite").read_bytes())

    def test_an_empty_callback_directory_records_nothing(self):
        base = Path(tempfile.mkdtemp()) / "ara"
        self.addCleanup(subprocess.run, ["rm", "-rf", str(base.parent)])
        empty = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, empty)
        self.run_play(base, empty)
        self.assertFalse((base / "ansible.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
