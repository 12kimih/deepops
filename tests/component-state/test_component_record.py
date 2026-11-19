"""roles/component_record: owner sets, orphans, and a real run on scratch paths."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "component_record", ROOT / "roles/component_record/library/component_record.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def run(data, name, action, items=(), version=""):
    return mod.apply(data, name, action, list(items), version)


EMPTY = {"components": {}, "items": {}}


class OwnerSets(unittest.TestCase):
    def test_a_shared_item_goes_with_its_last_owner(self):
        rec, orphans, _ = run(EMPTY, "server", "present", ["package:rsyslog", "file:/etc/a"])
        rec, orphans, _ = run(rec, "client", "present", ["package:rsyslog", "file:/etc/b"])
        rec, orphans, before = run(rec, "server", "absent")
        self.assertEqual(before, {"state": "present", "version": ""})
        self.assertEqual(orphans["file"], ["/etc/a"])
        self.assertEqual(orphans["package"], [], "the client still needs rsyslog")
        rec, orphans, _ = run(rec, "client", "absent")
        self.assertEqual((orphans["package"], orphans["file"]), (["rsyslog"], ["/etc/b"]))
        self.assertEqual(rec, EMPTY)

    def test_a_new_version_drops_only_the_old_versions_items(self):
        rec, _, _ = run(EMPTY, "sdk", "present", ["dir:/sw/sdk/1", "file:/etc/profile.d/sdk.sh"], "1")
        rec, orphans, _ = run(rec, "sdk", "begin", ["dir:/sw/sdk/2"], "2")
        self.assertEqual(orphans["dir"], [], "begin only adds")
        self.assertEqual(rec["components"]["sdk"]["state"], "installing")
        rec, orphans, _ = run(rec, "sdk", "present", ["dir:/sw/sdk/2", "file:/etc/profile.d/sdk.sh"], "2")
        self.assertEqual(orphans["dir"], ["/sw/sdk/1"])
        self.assertEqual(rec["components"]["sdk"], {"state": "present", "version": "2"})

    def test_absent_adopts_what_an_unrecorded_host_has(self):
        rec, orphans, before = run(EMPTY, "old", "absent", ["package:ypserv"])
        self.assertEqual((before, orphans["package"], rec), ({}, ["ypserv"], EMPTY))

    def test_adoption_never_takes_an_item_another_component_needs(self):
        rec, _, _ = run(EMPTY, "ood", "present", ["package:apache2"])
        _, orphans, _ = run(rec, "old", "absent", ["package:apache2"])
        self.assertEqual(orphans["package"], [])


PLAY = """
- hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - ansible.builtin.include_role:
        name: component_record
      vars:
        component_record_path: "{{ record }}"
        component_record_name: demo
        component_record_action: "{{ action }}"
        component_record_items: "{{ items }}"
"""


class Run(unittest.TestCase):
    """The role itself, on files and directories in a scratch directory."""

    def play(self, action, items, check=False):
        argv = ["ansible-playbook", "-i", "localhost,", str(self.playbook), "-e", json.dumps(
            {"record": str(self.record), "action": action, "items": items})]  # fmt: skip
        done = subprocess.run(argv + (["--check"] if check else []), cwd=ROOT, capture_output=True, text=True,
                              env={**os.environ, "ANSIBLE_CALLBACK_RESULT_FORMAT": "json"})  # fmt: skip
        self.assertEqual(done.returncode, 0, done.stdout[-3000:] + done.stderr[-2000:])
        return int(done.stdout.rsplit("changed=", 1)[1].split()[0])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.playbook = self.base / "play.yml"
        self.playbook.write_text(PLAY)
        self.record = self.base / "record.fact"
        (self.base / "conf").mkdir()
        for name in ("a", "b"):
            (self.base / "conf" / name).write_text(name)
        (self.base / "data").mkdir()
        (self.base / "data" / "keep").write_text("data")

    def test_present_rerun_shrink_absent(self):
        conf = self.base / "conf"
        items = [f"file:{conf}/a", f"file:{conf}/b", f"dir:{conf}", f"dir:{self.base}/data"]
        self.assertGreater(self.play("present", items), 0)
        self.assertEqual(self.play("present", items), 0, "a converged rerun changes nothing")
        self.play("present", items[1:], check=True)
        self.assertTrue((conf / "a").exists(), "--check removes nothing")
        self.play("present", items[1:])
        self.assertFalse((conf / "a").exists(), "what the new set no longer needs goes")
        self.play("absent", [])
        self.assertFalse(conf.exists(), "an emptied directory goes")
        self.assertTrue((self.base / "data" / "keep").exists(), "a directory with anything in it stays")
        self.assertEqual(json.loads(self.record.read_text()), EMPTY)
        self.assertEqual(self.play("absent", []), 0)


if __name__ == "__main__":
    unittest.main()
