"""roles/netplan on scratch configurations, under --check: what it writes, what it sets
aside, and that every change goes through the way back (apply.yml)."""

import base64
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import unrecorded  # noqa: E402

CONFIG = {
    "version": 2,
    "renderer": "networkd",
    "ethernets": {"eth0": {"addresses": ["10.0.0.5/16"], "dhcp4": False}},
}
HAND = (
    "network:\n  version: 2\n  renderer: networkd\n  ethernets:\n    eth0: {dhcp4: false, addresses: [%s]}\n"
)
PLAY = """
- hosts: localhost
  connection: local
  gather_facts: false
  roles: [netplan]
  post_tasks:
    - ansible.builtin.debug:
        msg: "DECIDED={{ {'differs': netplan_differs | bool, 'foreign': netplan_foreign | map('basename') | list} | to_json | b64encode }}"
"""


class Netplan(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.dir = self.base / "netplan"
        self.dir.mkdir()
        (self.base / "play.yml").write_text(PLAY)

    def check(self):
        extra = {"netplan_dir": str(self.dir), "deepops_state_dir": str(self.base), "netplan_config": CONFIG,
                 "netplan_wait_online": False}  # fmt: skip
        done = subprocess.run(
            ["ansible-playbook", "-i", "localhost,", "--check", str(self.base / "play.yml"), "-e", json.dumps(extra)],
            cwd=ROOT, capture_output=True, text=True, timeout=unrecorded.TIMEOUT,
            env=unrecorded.env(ANSIBLE_ROLES_PATH=str(ROOT / "roles")),
        )  # fmt: skip
        self.assertEqual(done.returncode, 0, done.stdout[-3000:] + done.stderr[-2000:])
        decided = json.loads(base64.b64decode(re.search(r"DECIDED=([A-Za-z0-9+/=]+)", done.stdout).group(1)))
        return decided, done.stdout

    def test_a_host_already_configured_so_is_left_alone(self):
        (self.dir / "01-netcfg.yaml").write_text(HAND % "10.0.0.5/16")  # another layout, same data
        (self.dir / "60-bmc-mgmt.yaml").write_text("# Ansible managed\nnetwork:\n  version: 2\n")
        decided, out = self.check()
        self.assertEqual(decided, {"differs": False, "foreign": []})
        self.assertIn("changed=0", out)

    def test_a_file_no_deepops_role_wrote_is_set_aside_through_the_way_back(self):
        (self.dir / "01-netcfg.yaml").write_text(HAND % "10.0.0.5/16")
        (self.dir / "60-bmc-mgmt.yaml").write_text("# Ansible managed\nnetwork:\n  version: 2\n")
        (self.dir / "90-NM-x.yaml").write_text("network:\n  version: 2\n")
        decided, out = self.check()
        self.assertEqual(decided, {"differs": False, "foreign": ["90-NM-x.yaml"]})
        self.assertIn("Arm the way back", out)
        self.assertNotIn("Write ", out.split("Set aside the files", 1)[0].split("Keep the current", 1)[1])

    def test_a_change_is_written_and_applied_with_the_way_back(self):
        (self.dir / "01-netcfg.yaml").write_text(HAND % "10.0.0.9/16")
        decided, out = self.check()
        self.assertEqual(decided, {"differs": True, "foreign": []})
        for step in (
            "Keep the current configuration",
            "Arm the way back",
            "Preview applying",
            "Disarm the way back",
        ):
            self.assertIn(step, out)


if __name__ == "__main__":
    unittest.main()
