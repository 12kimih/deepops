"""plugins/action/systemd_dropin.py run by Ansible: a unit's drop-in is written with the
directory it goes in (no package ships one), previewed under --check, and removed with
that directory once nothing else is in it."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import unrecorded  # noqa: E402

sys.path.insert(0, str(ROOT / "plugins" / "action"))
from systemd_dropin import check  # noqa: E402

PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")


def run(base, tasks, *extra):
    """Each task's result by name, from a play of `tasks` (YAML, the drop-ins under base)."""
    play = f"""
- hosts: localhost
  gather_facts: false
  connection: local
  vars: {{base: "{base}"}}
  tasks:
{tasks}
"""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "play.yml").write_text(play)
        env = unrecorded.env(
            ANSIBLE_ACTION_PLUGINS=str(ROOT / "plugins" / "action"),
            ANSIBLE_LOAD_CALLBACK_PLUGINS="1",
            ANSIBLE_STDOUT_CALLBACK="ansible.builtin.json",
            ANSIBLE_LOCALHOST_WARNING="false",
        )
        done = subprocess.run([str(PLAYBOOK), "-i", "localhost,", "play.yml", *extra], cwd=tmp, env=env,
                              capture_output=True, text=True, timeout=unrecorded.TIMEOUT)  # fmt: skip
    tasks = json.loads(done.stdout)["plays"][0]["tasks"]
    return {t["task"]["name"]: t["hosts"]["localhost"] for t in tasks}


WRITE = """    - name: write
      systemd_dropin:
        path: "{{ base }}/x.service.d/10-a.conf"
        content: "[Unit]\\nAfter=y.service\\n"
        daemon_reload: false
"""
REMOVE = """    - name: remove
      systemd_dropin:
        path: "{{ base }}/x.service.d/10-a.conf"
        state: absent
        daemon_reload: false
"""


class Arguments(unittest.TestCase):
    def test_a_drop_in_is_named_by_its_unit(self):
        self.assertEqual(check({"path": "/etc/systemd/system/slurmd.service.d/a.conf", "content": ""}), "")
        self.assertEqual(
            check({"path": "/etc/systemd/system/user-.slice.d/50-limits.conf", "content": ""}), ""
        )
        for path in ("/etc/systemd/system/slurmd.service", "/etc/systemd/system/x.d/a.conf",
                     "/etc/systemd/resolved.conf.d/a.conf", "slurmd.service.d/a.conf",
                     "/etc/systemd/system/slurmd.service.d/a"):  # fmt: skip
            self.assertIn("drop-in", check({"path": path, "content": ""}), path)

    def test_content_goes_with_present_only(self):
        path = "/etc/systemd/system/x.service.d/a.conf"
        self.assertIn("needs content", check({"path": path}))
        self.assertIn("no content", check({"path": path, "state": "absent", "content": ""}))
        self.assertIn("present or absent", check({"path": path, "state": "latest"}))
        self.assertIn("unknown", check({"path": path, "content": "", "dest": path}))


class SystemdDropin(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", "--", str(self.base)], check=True)
        self.dropin = self.base / "x.service.d" / "10-a.conf"

    def test_a_preview_writes_nothing_and_shows_the_file(self):
        got = run(self.base, WRITE, "--check", "--diff")["write"]
        self.assertTrue(got["changed"] and not got.get("failed"), got)
        self.assertIn("After=y.service", got["diff"][-1]["after"])
        self.assertFalse(self.dropin.parent.exists())

    def test_the_directory_is_made_and_the_rerun_is_unchanged(self):
        got = run(self.base, WRITE)["write"]
        self.assertTrue(got["changed"] and not got.get("failed"), got)
        self.assertEqual(self.dropin.read_text(), "[Unit]\nAfter=y.service\n")
        self.assertEqual(self.dropin.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.dropin.parent.stat().st_mode & 0o777, 0o755)
        self.assertFalse(run(self.base, WRITE)["write"]["changed"])

    def test_removal_takes_the_directory_once_empty(self):
        run(self.base, WRITE)
        preview = run(self.base, REMOVE, "--check")["remove"]
        self.assertTrue(preview["changed"] and self.dropin.exists(), preview)
        got = run(self.base, REMOVE)["remove"]
        self.assertTrue(got["changed"] and not got.get("failed"), got)
        self.assertFalse(self.dropin.parent.exists())
        self.assertFalse(run(self.base, REMOVE)["remove"]["changed"], "gone already")

    def test_removal_keeps_a_directory_that_holds_more(self):
        run(self.base, WRITE)
        other = self.dropin.with_name("90-site.conf")
        other.write_text("[Unit]\n")
        got = run(self.base, REMOVE)["remove"]
        self.assertTrue(got["changed"], got)
        self.assertFalse(self.dropin.exists())
        self.assertTrue(other.exists())

    def test_a_bad_path_fails_the_task(self):
        bad = WRITE.replace("x.service.d/10-a.conf", "x.d/10-a.conf")
        self.assertTrue(run(self.base, bad)["write"].get("failed"))


if __name__ == "__main__":
    unittest.main()
