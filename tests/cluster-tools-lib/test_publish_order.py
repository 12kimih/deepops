"""roles/cluster_tools_lib checks the recorded tool configs against the package it is
about to publish, and publishes only when every one is taken.

The package went live first and was checked after (a handler): when a tool gained a
required key, the live hourly usage report and daily disk report refused their configs
until their own playbooks ran (ARA 243, 2026-11-23). The check now runs on a staged
copy before the swap: a refusal leaves the live package as it was."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import unrecorded  # noqa: E402

PLAY = """
- hosts: localhost
  gather_facts: false
  tasks:
    - ansible.builtin.include_role:
        name: cluster_tools_lib
"""


class PublishOrder(unittest.TestCase):
    def run_play(self, config):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        live = d / "lib" / "cluster_tools"
        live.mkdir(parents=True)
        (live / "marker.py").write_text("LIVE = True\\n")  # the package running now
        records = d / "etc" / "configs.d"
        records.mkdir(parents=True)
        cfg = d / "etc" / "submit-holds.json"
        cfg.write_text(json.dumps(config))
        (records / "submit_holds.json").write_text(json.dumps(
            {"role": "submit_holds", "module": "cluster_tools.submit_holds", "config": str(cfg),
             "playbook": "playbooks/slurm-cluster/submit-holds.yml"}))  # fmt: skip
        (d / "play.yml").write_text(PLAY)
        env = unrecorded.env(ANSIBLE_ROLES_PATH=str(ROOT / "roles"), HOME=str(d))
        done = subprocess.run(
            [str(Path(sys.executable).with_name("ansible-playbook")), "-i", "localhost,", "-c", "local",
             str(d / "play.yml"), "-e", f"cluster_tools_lib_dir={d / 'lib'}",
             "-e", f"cluster_tools_lib_config_dir={d / 'etc'}", "-e", f"cluster_tools_lib_python={sys.executable}",
             "-e", "ansible_become=false"],
            capture_output=True, text=True, env=env, cwd=ROOT, timeout=unrecorded.TIMEOUT,
        )  # fmt: skip
        return done, live

    def test_a_refused_config_stops_the_run_before_the_live_package_changes(self):
        done, live = self.run_play({"file": "/x", "textfile": "/y"})  # lacks most keys
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("The live package is unchanged", done.stdout)
        self.assertIn("rerun playbooks/slurm-cluster/submit-holds.yml", done.stdout)
        self.assertEqual(sorted(os.listdir(live)), ["marker.py"], "the live package must stay as it was")
