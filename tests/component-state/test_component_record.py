"""roles/component_record: owner sets, orphans, and a real run on scratch paths."""

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

sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402

mod = in_tree.load("roles/component_record/library/component_record.py")
marks = sys.modules["ansible.module_utils.deepops_marks"]


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

    def test_a_converged_rerun_leaves_the_record_as_it_is(self):
        items = ["package:x", "file:/etc/x"]
        rec, _, _ = run(EMPTY, "c", "begin", items, "1")
        rec, _, _ = run(rec, "c", "present", items, "1")
        again, _, _ = run(rec, "c", "begin", items, "1")
        self.assertEqual(again, rec)
        self.assertEqual(run(again, "c", "present", items, "1")[0], rec)
        # A role that names the version only at present (begin without one) converges too.
        again, _, _ = run(rec, "c", "begin", items, "")
        self.assertEqual(again, rec)
        for other in ((items + ["file:/etc/y"], "1"), (items, "2")):
            with self.subTest(change=other):
                self.assertEqual(run(rec, "c", "begin", *other)[0]["components"]["c"]["state"], "installing")

    def test_absent_adopts_what_an_unrecorded_host_has(self):
        marked = {("file", "/etc/yp.conf")}
        rec, orphans, before = mod.apply(
            EMPTY, "old", "absent", ["package:ypserv", "file:/etc/yp.conf"], "", {"ypserv"},
            lambda kind, ident: (kind, ident) in marked,
        )  # fmt: skip
        self.assertEqual(
            (before, orphans["package"], orphans["file"], rec), ({}, ["ypserv"], ["/etc/yp.conf"], EMPTY)
        )

    def test_without_evidence_a_sites_own_file_or_service_is_never_adopted(self):
        # Docker's documented proxy drop-in name, a site's own: no mark, so it stays.
        items = ["file:/etc/systemd/system/docker.service.d/http-proxy.conf", "unit:unbound"]
        _, orphans, _ = mod.apply(EMPTY, "cache_client", "absent", items, "")
        self.assertEqual((orphans["file"], orphans["unit"]), ([], []))

    def test_containers_and_directories_follow_their_evidence(self):
        items = [
            "unit:docker.prometheus.service", "container:docker.prometheus.service",
            "container:registry", "container:deepops-registry",
            "file:/etc/prometheus/prometheus.yml", "dir:/etc/prometheus", "dir:/etc/site", "dir:/etc/deepops/ca",
        ]  # fmt: skip
        marked = {("unit", "docker.prometheus.service"), ("file", "/etc/prometheus/prometheus.yml")}
        got = mod.adoptable(
            items, set(), (), lambda kind, ident: (kind, ident) in marked or "deepops" in ident
        )
        self.assertEqual(
            sorted(got),
            sorted([
                "unit:docker.prometheus.service", "container:docker.prometheus.service",
                "container:deepops-registry", "file:/etc/prometheus/prometheus.yml",
                "dir:/etc/prometheus", "dir:/etc/deepops/ca",
            ]),
        )  # fmt: skip

    def test_a_recorded_item_needs_no_evidence(self):
        rec, _, _ = run(EMPTY, "client", "present", ["file:/etc/generic.conf"])
        _, orphans, _ = run(rec, "client", "absent", ["file:/etc/generic.conf"])
        self.assertEqual(orphans["file"], ["/etc/generic.conf"])

    def test_a_mount_goes_with_its_last_owner_and_is_adopted(self):
        rec, orphans, _ = run(EMPTY, "client", "present", ["mount:/home", "mount:/sw"])
        rec, orphans, _ = run(rec, "client", "present", ["mount:/home"])
        self.assertEqual(orphans["mount"], ["/sw"], "a mount no longer listed goes")
        _, orphans, _ = run(EMPTY, "client", "absent", ["mount:/scratch"])
        self.assertEqual(orphans["mount"], ["/scratch"], "mounts adopt like files")

    def test_absent_adopts_no_package_it_was_not_told_is_its_own(self):
        # python3-docker, unrecorded on this host, may serve a role that records nothing.
        _, orphans, _ = run(EMPTY, "old", "absent", ["package:python3-docker"])
        self.assertEqual(orphans["package"], [])
        rec, _, _ = run(EMPTY, "old", "present", ["package:python3-docker"])
        self.assertEqual(run(rec, "old", "absent")[1]["package"], ["python3-docker"], "recorded: freed")

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
        component_record_action: "{{ 'begin' if action == 'present' else action }}"
        component_record_items: "{{ items }}"
    - ansible.builtin.include_role:
        name: component_record
      vars:
        component_record_path: "{{ record }}"
        component_record_name: demo
        component_record_action: present
        component_record_items: "{{ items }}"
      when: action == 'present'
    - ansible.builtin.debug:
        msg: "REMOVED={{ component_record_removed | to_json | b64encode }}"
"""


class Run(unittest.TestCase):
    """The role itself, on files and directories in a scratch directory."""

    def play(self, action, items, check=False):
        argv = ["ansible-playbook", "-i", "localhost,", str(self.playbook), "-e", json.dumps(
            {"record": str(self.record), "action": action, "items": items})]  # fmt: skip
        done = subprocess.run(argv + (["--check"] if check else []), cwd=ROOT, capture_output=True, text=True,
                              env=unrecorded.env(ANSIBLE_CALLBACK_RESULT_FORMAT="json"))  # fmt: skip
        self.assertEqual(done.returncode, 0, done.stdout[-3000:] + done.stderr[-2000:])
        encoded = re.search(r"REMOVED=([A-Za-z0-9+/=]+)", done.stdout).group(1)
        self.removed = json.loads(base64.b64decode(encoded))
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
        self.assertEqual(self.removed["file"], [f"{conf}/b"])
        self.assertEqual(self.removed["dir"], [str(conf)], "only what was there")
        self.assertFalse(conf.exists(), "an emptied directory goes")
        self.assertTrue((self.base / "data" / "keep").exists(), "a directory with anything in it stays")
        self.assertEqual(json.loads(self.record.read_text()), EMPTY)
        self.assertEqual(self.play("absent", []), 0)
        # Adopted on a host that never had them: orphans, but nothing removed.
        self.assertEqual(self.play("absent", [f"file:{conf}/never"]), 0)
        self.assertEqual(self.removed["file"], [], "an adopted orphan that was never there")


if __name__ == "__main__":
    unittest.main()
