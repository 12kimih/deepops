"""Every Ansible run is recorded with ARA; test runs are not; no_log values never are.

The operator ran a playbook, copied its output and pasted it to whoever reviewed it,
often cut short. ARA (ansible.cfg callback_plugins -> plugins/callback, the package's
recorder with names and paths bounded) keeps each run's tasks, results and diffs in
~/.ara/server, read with `ara`. scripts/check.sh and scripts/deepops/molecule.py
point ANSIBLE_CALLBACK_PLUGINS at an empty directory, so test playbooks leave no record.
"""

import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import unrecorded  # noqa: E402

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


CALLBACKS = ROOT / "plugins" / "callback"
LIMIT = 255  # the characters ARA's server stores in a path or a name
LONG = "x" * 300  # an ad-hoc command longer than that


class Wiring(unittest.TestCase):
    def test_ansible_cfg(self):
        cfg = configparser.ConfigParser()
        cfg.read(ROOT / "ansible.cfg")
        self.assertEqual(cfg["defaults"]["callback_plugins"], "./plugins/callback")
        self.assertEqual(
            sorted(p.name for p in tracked("plugins/callback/*")), ["ara_default.py", "run_lock.py"]
        )

    def test_quoted_settings_match_ansible_cfg(self):
        """A comment, docstring or document quoting an ansible.cfg setting (`key = value`)
        quotes the file: one still named .callback_plugins after the callback moved."""
        cfg = configparser.ConfigParser()
        cfg.read(ROOT / "ansible.cfg")
        settings = dict(cfg["defaults"])
        # As prose quotes one: after "(" or a backtick, never a Python assignment.
        quoted = re.compile(rf"[(`]({'|'.join(map(re.escape, settings))}) = ([^\s`)]+)")
        wrong = []
        for path in tracked("*.py", "*.md", "*.yml", "*.sh", "*.j2", "*.cfg"):
            if path.name == "ansible.cfg" or "submodules" in path.parts:
                continue
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                for key, value in quoted.findall(line):
                    actual = settings[key]
                    if not (actual.startswith(value[:-3]) if value.endswith("...") else value == actual):
                        wrong.append(
                            f"{path.relative_to(ROOT)}:{number}: {key} = {value} (ansible.cfg: {actual})"
                        )
        self.assertEqual(wrong, [])

    def test_setup_drops_the_old_link_only(self):
        """Earlier setup.sh linked .callback_plugins to the package's directory: setup.sh's
        own lines remove that link, and leave anything else by that name."""
        setup = (ROOT / "scripts/setup.sh").read_text()
        self.assertNotIn("ara.setup.callback_plugins", setup)
        block = re.search(r'^case "\$\(readlink \.callback_plugins.*?^esac$', setup, re.M | re.S)
        self.assertIsNotNone(block, "setup.sh removes the old .callback_plugins link")
        package = subprocess.run(
            [sys.executable, "-m", "ara.setup.callback_plugins"], capture_output=True, text=True, check=True
        ).stdout.strip()
        for kind, gone in (("ara link", True), ("other link", False), ("file", False)):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                old = Path(tmp, ".callback_plugins")
                if kind == "file":
                    old.write_text("mine\n")
                else:
                    old.symlink_to(package if kind == "ara link" else tmp)
                subprocess.run(
                    ["bash", "-c", block.group(0)], cwd=tmp, check=True, timeout=unrecorded.TIMEOUT
                )
                self.assertEqual(not (old.exists() or old.is_symlink()), gone)

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
            elif rel.endswith(".py") and "subprocess" in text and "timeout=unrecorded.TIMEOUT" not in text:
                offenders.append(f"{rel}: bound the run with timeout=unrecorded.TIMEOUT")
        self.assertEqual(offenders, [], "run Ansible with unrecorded.env() (scripts/deepops/unrecorded.py)")


class Recording(unittest.TestCase):
    def run_play(self, base, callbacks, play=PLAY):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(d)])
        (d / "play.yml").write_text(play)
        base.mkdir(parents=True, exist_ok=True)  # the run's HOME: Ansible warns when it is missing
        # unrecorded.env for its throwaway vault password; the callbacks are this test's own.
        # HOME: run_lock's lock (plugins/callback) in the throwaway base, not shared with
        # another checkout's test running the same play at the same time.
        env = {**unrecorded.env(), "ARA_BASE_DIR": str(base), "ANSIBLE_CALLBACK_PLUGINS": str(callbacks),
               "HOME": str(base),
               "ANSIBLE_CONFIG": str(ROOT / "ansible.cfg"), "ANSIBLE_LOCALHOST_WARNING": "false",
               # facts gathered every time, into memory, never the operator's fact cache
               "ANSIBLE_GATHERING": "implicit"}  # fmt: skip
        out = subprocess.run(
            [str(BIN / "ansible-playbook"), "-i", "localhost,", str(d / "play.yml")],
            env=env, capture_output=True, text=True, timeout=unrecorded.TIMEOUT,
        )  # fmt: skip
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return env

    def ara(self, env, *args):
        out = subprocess.run([str(BIN / "ara"), *args, "-f", "json"], env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_a_run_is_recorded_without_secrets(self):
        base = Path(tempfile.mkdtemp()) / "ara"
        self.addCleanup(subprocess.run, ["rm", "-rf", str(base.parent)])
        env = self.run_play(base, CALLBACKS)
        playbooks = self.ara(env, "playbook", "list")
        self.assertEqual([p["status"] for p in playbooks], ["completed"])
        content = json.dumps([self.ara(env, "result", "show", str(r["id"]), "--with-content")
                              for r in self.ara(env, "result", "list")])  # fmt: skip
        self.assertIn("visible-output", content)
        self.assertNotIn(SECRET, content)
        self.assertNotIn(SECRET.encode(), (base / "ansible.sqlite").read_bytes())

    def test_a_warning_shown_on_the_terminal_only_is_recorded(self):
        # A reserved variable name: Ansible warns on the display, in no task result.
        play = PLAY.replace("  tasks:\n", "  vars:\n    action: x\n  tasks:\n", 1)
        self.assertNotEqual(play, PLAY)
        base = Path(tempfile.mkdtemp()) / "ara"
        self.addCleanup(subprocess.run, ["rm", "-rf", str(base.parent)])
        env = self.run_play(base, CALLBACKS, play)
        (playbook,) = self.ara(env, "playbook", "list")
        records = {r["key"]: r for r in self.ara(env, "record", "list", "--playbook", str(playbook["id"]))}
        value = self.ara(env, "record", "show", str(records["warnings"]["id"]))["value"]
        self.assertTrue(any("reserved name" in w for w in value), value)
        shown = self.ara(env, "playbook", "show", str(playbook["id"]))
        self.assertIn("warnings", [label["name"] for label in shown["labels"]])

    def test_a_run_without_warnings_records_none(self):
        base = Path(tempfile.mkdtemp()) / "ara"
        self.addCleanup(subprocess.run, ["rm", "-rf", str(base.parent)])
        env = self.run_play(base, CALLBACKS)
        (playbook,) = self.ara(env, "playbook", "list")
        self.assertEqual(self.ara(env, "record", "list", "--playbook", str(playbook["id"])), [])

    def test_the_display_still_keeps_what_it_showed(self):
        # The recorder reads the display's own sets: an ansible-core that renames them
        # fails here, not by recording nothing.
        from ansible.utils.display import Display

        self.assertIsInstance(Display()._warns, set)
        self.assertIsInstance(Display()._deprecations, set)

    def run_adhoc(self, base, callbacks, command=None):
        """A recorded ad-hoc command of more than 255 characters; its output."""
        env = {**unrecorded.env(), "ARA_BASE_DIR": str(base), "ANSIBLE_CALLBACK_PLUGINS": str(callbacks),
               "ANSIBLE_CONFIG": str(ROOT / "ansible.cfg"), "ANSIBLE_LOCALHOST_WARNING": "false"}  # fmt: skip
        out = subprocess.run(
            [str(BIN / "ansible"), "-i", "localhost,", "localhost", "-c", "local", "-m", "ansible.builtin.command",
             "-a", command or f"echo {LONG}"],
            env=env, capture_output=True, text=True, timeout=unrecorded.TIMEOUT,
        )  # fmt: skip
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return env, out.stdout + out.stderr

    def test_a_long_ad_hoc_command_is_recorded(self):
        base = Path(tempfile.mkdtemp()) / "ara"
        self.addCleanup(subprocess.run, ["rm", "-rf", str(base.parent)])
        env, output = self.run_adhoc(base, CALLBACKS)
        self.assertNotIn("WARNING", output)
        [playbook] = self.ara(env, "playbook", "list")
        self.assertEqual(playbook["status"], "completed")
        shown = self.ara(env, "playbook", "show", str(playbook["id"]))  # list shortens paths for display
        self.assertTrue(shown["path"].startswith("Ad-Hoc: ansible.builtin.command: echo xxx"))
        self.assertEqual(len(shown["path"]), 255)
        self.assertEqual(shown["arguments"]["module_args"], f"echo {LONG}", "the whole command is kept")
        [result] = self.ara(env, "result", "list")
        self.assertEqual(result["status"], "changed", "a command is a change unless told otherwise")

    def test_a_cut_on_a_blank_is_recorded(self):
        """The server strips a trailing blank: the file is found again under that path."""
        prefix = "Ad-Hoc: ansible.builtin.command: echo "
        command = "echo " + "y" * (LIMIT - 1 - len(prefix)) + " " + "z" * 60
        self.assertEqual((prefix + command[5:])[LIMIT - 1], " ", "the cut ends on the blank")
        base = Path(tempfile.mkdtemp()) / "ara"
        self.addCleanup(subprocess.run, ["rm", "-rf", str(base.parent)])
        env, output = self.run_adhoc(base, CALLBACKS, command)
        self.assertNotIn("WARNING", output)
        self.assertNotIn("ERROR", output)
        [result] = self.ara(env, "result", "list")
        self.assertEqual(result["status"], "changed")

    def test_ara_alone_still_refuses_it(self):
        """ARA's own callback (1.8.0) cannot record it; when this fails, upstream bounds the
        path itself and plugins/callback can go back to the package's directory."""
        callbacks = subprocess.run(
            [sys.executable, "-m", "ara.setup.callback_plugins"], capture_output=True, text=True, check=True
        ).stdout.strip()
        base = Path(tempfile.mkdtemp()) / "ara"
        self.addCleanup(subprocess.run, ["rm", "-rf", str(base.parent)])
        _, output = self.run_adhoc(base, callbacks)
        self.assertIn("Failed to post on /api/v1/playbooks", output)

    def test_an_empty_callback_directory_records_nothing(self):
        base = Path(tempfile.mkdtemp()) / "ara"
        self.addCleanup(subprocess.run, ["rm", "-rf", str(base.parent)])
        empty = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, empty)
        self.run_play(base, empty)
        self.assertFalse((base / "ansible.sqlite").exists())


class NoSiteSecret(unittest.TestCase):
    def test_a_test_run_starts_where_no_site_config_is(self):
        """ansible.cfg names config/.vault-pass, and Ansible does not start without it: a
        test run takes unrecorded's throwaway password instead (CI has no site config)."""
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(ROOT / "ansible.cfg", tmp)
            base = {k: v for k, v in os.environ.items() if k != "ANSIBLE_VAULT_PASSWORD_FILE"}
            env = unrecorded.env(base, ANSIBLE_CONFIG=str(Path(tmp, "ansible.cfg")))
            self.assertNotIn(".vault-pass", env["ANSIBLE_VAULT_PASSWORD_FILE"])
            argv = [str(BIN / "ansible"), "localhost", "-m", "ansible.builtin.ping"]
            for case, run_env in (
                ("site file missing", base | {"ANSIBLE_CONFIG": env["ANSIBLE_CONFIG"]}),
                ("unrecorded", env),
            ):
                with self.subTest(case=case):
                    out = subprocess.run(
                        argv, cwd=tmp, env=run_env, capture_output=True, text=True, timeout=unrecorded.TIMEOUT
                    )
                    self.assertEqual(out.returncode == 0, case == "unrecorded", out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
