"""plugins/action/change_command.py and change_shell.py, run by Ansible on localhost.

Under --check a change is reported (changed, the command as the diff) and nothing runs;
without it they run exactly as command and shell; creates/removes stay command's call.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import unrecorded  # noqa: E402

PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")

PLAY = """
- hosts: localhost
  gather_facts: false
  tasks:
    - name: Argv
      change_command:
        argv: [touch, "{{ out }}/argv"]
      register: r_argv
    - name: Cmd in a directory
      change_command:
        cmd: touch cmd
        chdir: "{{ out }}"
      register: r_cmd
    - name: Shell
      change_shell:
        cmd: echo hi > {{ out }}/shell && echo done
      register: r_shell
    - name: Creates an existing path
      change_command:
        argv: [touch, "{{ out }}/never"]
        creates: "{{ out }}"
      register: r_creates
    - name: Guarded condition on the result
      change_command:
        argv: [echo, made]
      register: r_guarded
      changed_when: ansible_check_mode or ('made' in r_guarded.stdout)
    - name: Unless a probe holds
      change_command:
        argv: [touch, "{{ out }}/held"]
        unless: [test, -d, "{{ out }}"]
      register: r_held
    - name: Unless a probe fails
      change_command:
        argv: [touch, "{{ out }}/unheld"]
        unless: [test, -e, "{{ out }}/unheld"]
      register: r_unheld
    - name: Record
      ansible.builtin.copy:
        content: "{{ {'argv': r_argv, 'cmd': r_cmd, 'shell': r_shell, 'creates': r_creates,
                     'guarded': r_guarded, 'held': r_held, 'unheld': r_unheld} | to_json }}"
        dest: "{{ record }}"
        mode: "0600"
      check_mode: false
"""


def run(tmp, *extra):
    out, record = tmp / "out", tmp / "record.json"
    out.mkdir(exist_ok=True)
    (tmp / "play.yml").write_text(PLAY)
    env = unrecorded.env(ANSIBLE_ACTION_PLUGINS=str(ROOT / "plugins" / "action"))
    env.pop("ANSIBLE_CONFIG", None)
    result = subprocess.run(
        [str(PLAYBOOK), "-i", "localhost,", "-c", "local", "-e", f"out={out}", "-e", f"record={record}",
         str(tmp / "play.yml"), *extra],
        capture_output=True, text=True, env=env, cwd=tmp, timeout=unrecorded.TIMEOUT,
    )  # fmt: skip
    if result.returncode:
        raise AssertionError(result.stdout[-3000:] + result.stderr[-2000:])
    return json.loads(record.read_text()), sorted(p.name for p in out.iterdir()), result.stdout


class ChangeCommand(unittest.TestCase):
    def test_check_reports_and_runs_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            got, files, stdout = run(Path(tmp), "--check", "--diff")
        self.assertEqual(files, [])
        for key in ("argv", "cmd", "shell", "guarded", "unheld"):
            self.assertTrue(got[key]["changed"], key)
            self.assertNotIn("rc", got[key], key)
        self.assertTrue(got["argv"]["cmd"].startswith("touch ") and got["argv"]["cmd"].endswith("/argv"))
        self.assertEqual(got["cmd"]["cmd"], f"cd {Path(tmp) / 'out'} && touch cmd")
        self.assertIn("would run: echo hi > ", stdout)
        self.assertFalse(got["creates"]["changed"])
        # unless runs its read-only probe in a preview too: what is there is no change.
        self.assertFalse(got["held"]["changed"])
        self.assertIn("not needed", got["held"]["msg"])

    def test_without_check_it_is_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            got, files, _ = run(Path(tmp))
        self.assertEqual(files, ["argv", "cmd", "shell", "unheld"])
        self.assertEqual((got["argv"]["rc"], got["shell"]["stdout"]), (0, "done"))
        self.assertTrue(got["argv"]["changed"] and got["guarded"]["changed"])
        self.assertFalse(got["creates"]["changed"])
        self.assertFalse(got["held"]["changed"])
        self.assertTrue(got["unheld"]["changed"])


if __name__ == "__main__":
    unittest.main()
