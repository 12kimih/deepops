"""plugins/action/systemd_unit.py run by Ansible: a unit this run would install is
previewed under --check instead of failing, and everything else is systemd_service's."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import unrecorded  # noqa: E402

sys.path.insert(0, str(ROOT / "tests" / "lib"))
import bounds  # noqa: E402

PLAYBOOK = Path(sys.executable).with_name("ansible-playbook")
MISSING = "deepops-test-not-installed.service"
PLAY = f"""
- hosts: localhost
  gather_facts: false
  connection: local
  tasks:
    - name: start
      systemd_unit: {{name: {MISSING}, state: started, enabled: true, daemon_reload: true}}
    - name: stop
      systemd_unit: {{name: {MISSING}, state: stopped, enabled: false}}
"""


def enabled_unit():
    """A service unit enabled on this host, for a relink preview."""
    out = subprocess.run(
        ["systemctl", "list-unit-files", "--type=service", "--state=enabled", "--no-legend", "--plain"],
        capture_output=True, text=True, timeout=bounds.CHILD,
    ).stdout  # fmt: skip
    return next((line.split()[0] for line in out.splitlines() if line.split()), None)


def run(*extra, play=PLAY):
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


@unittest.skipUnless(Path("/run/systemd/system").is_dir(), "needs systemd")
class SystemdUnit(unittest.TestCase):
    def test_a_unit_not_installed_yet_is_previewed(self):
        got = run("--check", "--diff")
        start, stop = got["start"], got["stop"]
        self.assertTrue(start["changed"] and not start.get("failed"), start)
        self.assertEqual(
            start["diff"]["prepared"],
            f"would reload systemd\nwould enable {MISSING}\nwould start {MISSING}\n",
        )
        self.assertFalse(stop["changed"] or stop.get("failed"), "stopping what is not there is done")

    def test_relink_previews_enabling_a_changed_unit_afresh(self):
        unit = enabled_unit()
        if not unit:
            self.skipTest("no enabled service unit")
        play = f"""
- hosts: localhost
  gather_facts: false
  connection: local
  tasks:
    - name: relink
      systemd_unit: {{name: {unit}, enabled: true, relink: true}}
    - name: unchanged
      systemd_unit: {{name: {unit}, enabled: true, relink: false}}
"""
        got = run("--check", "--diff", play=play)
        self.assertTrue(got["relink"]["changed"], got["relink"])
        self.assertEqual(got["relink"]["diff"]["prepared"], f"would reenable {unit}\n")
        self.assertFalse(got["unchanged"]["changed"], "an enabled unit with relink off stays as it is")

    def test_a_real_run_still_fails_on_a_missing_unit(self):
        got = run()
        self.assertTrue(got["start"].get("failed"), got["start"])


if __name__ == "__main__":
    unittest.main()
