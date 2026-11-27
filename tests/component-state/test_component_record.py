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

    def test_an_earlier_versions_unmarked_file_is_adopted_by_its_whole_shape(self):
        shape = [r'action\(type="omfwd" Target="[^"]+" Port="\d+" Protocol="(tcp|udp)"\)\n?']
        self.assertTrue(
            mod.legacy_match('action(type="omfwd" Target="m" Port="514" Protocol="tcp")\n', shape)
        )
        edited = 'action(type="omfwd" Target="m" Port="514" Protocol="tcp")\n*.* @@backup:514\n'
        self.assertFalse(mod.legacy_match(edited, shape), "one line more is a site's edit")
        self.assertFalse(mod.legacy_match("# my own forwarding\n", shape))
        self.assertFalse(mod.legacy_match("anything", []), "no shape, no adoption")

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
        component_record_legacy: "{{ legacy | default({}) }}"
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


class Sources(unittest.TestCase):
    """The source digest present records (component_source.py), and when it is kept."""

    def test_present_records_it_and_begin_and_a_bare_present_keep_it(self):
        first, _, _ = mod.apply(EMPTY, "c", "present", [], "1", source="aaaa", record_source="rrrr")
        self.assertEqual(first["components"]["c"], {"state": "present", "version": "1", "source": "aaaa"})
        self.assertEqual(first["record_source"], "rrrr")
        begun, _, _ = mod.apply(first, "c", "begin", ["file:/x"], "2")
        self.assertEqual(begun["components"]["c"]["source"], "aaaa", "an unfinished run keeps the old one")
        again, _, _ = mod.apply(begun, "c", "present", ["file:/x"], "2")
        self.assertEqual(again["components"]["c"]["source"], "aaaa", "a partial run records no new one")
        self.assertEqual(again["record_source"], "rrrr")
        newer, _, _ = mod.apply(again, "c", "present", ["file:/x"], "2", source="bbbb")
        self.assertEqual(newer["components"]["c"]["source"], "bbbb")

    def test_a_new_digest_alone_is_no_change(self):
        first, _, _ = mod.apply(EMPTY, "c", "present", [], "1", source="aaaa")
        newer, _, _ = mod.apply(first, "c", "present", [], "1", source="bbbb", record_source="r")
        self.assertNotEqual(newer, first)
        self.assertEqual(mod.bookkeeping(newer), mod.bookkeeping(first))

    def test_a_plays_end_records_present_components_and_managed_roles(self):
        """action sources (tasks_from: sources): what a whole play deployed."""
        base, _, _ = mod.apply(EMPTY, "c", "present", ["file:/x"], "1")
        base, _, _ = mod.apply(base, "d", "begin", ["file:/y"], "1")
        got, orphans, _ = mod.apply(base, "", "sources", [], "", source={"c": "aaaa", "d": "dddd", "gone": "g"},
                                    record_source="rrrr", managed={"sshd": "ssss"})  # fmt: skip
        self.assertEqual(got["components"]["c"]["source"], "aaaa")
        self.assertNotIn("source", got["components"]["d"], "installing: not deployed yet")
        self.assertNotIn("gone", got["components"], "an absent component is never brought back")
        self.assertEqual((got["managed"], got["record_source"]), ({"sshd": "ssss"}, "rrrr"))
        self.assertEqual(got["items"], base["items"])
        self.assertFalse(any(orphans.values()))
        self.assertEqual(mod.bookkeeping(got), mod.bookkeeping(base), "bookkeeping, never a change")
        kept, _, _ = mod.apply(got, "c", "present", ["file:/x"], "1")
        self.assertEqual((kept["managed"], kept["components"]["c"]["source"]), ({"sshd": "ssss"}, "aaaa"))


class UnitOrder(unittest.TestCase):
    def test_what_starts_a_service_stops_before_it(self):
        _, orphans, _ = run(
            {"components": {"c": {"state": "present", "version": ""}},
             "items": {"unit:x.service": ["c"], "unit:x.timer": ["c"], "unit:y.socket": ["c"], "unit:a.service": ["c"]}},
            "c", "absent",
        )  # fmt: skip
        self.assertEqual(orphans["unit"], ["x.timer", "y.socket", "a.service", "x.service"])


class Run(unittest.TestCase):
    """The role itself, on files and directories in a scratch directory."""

    def play(self, action, items, check=False, legacy=None, fails=False):
        argv = ["ansible-playbook", "-i", "localhost,", str(self.playbook), "-e", json.dumps(
            {"record": str(self.record), "action": action, "items": items, "legacy": legacy or {}})]  # fmt: skip
        done = subprocess.run(argv + (["--check"] if check else []), cwd=ROOT, capture_output=True, text=True,
                              env=unrecorded.env(ANSIBLE_CALLBACK_RESULT_FORMAT="json"), timeout=unrecorded.TIMEOUT)  # fmt: skip
        if fails:
            self.assertNotEqual(done.returncode, 0, done.stdout[-3000:])
            return None
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
        # A directory named deepops a host never had is no evidence: a preview of the
        # removal on such a host (grafana's dashboards on a node without Grafana) is clean.
        self.assertEqual(self.play("absent", [f"dir:{self.base}/dashboards/deepops"], check=True), 0)

    def test_a_removal_that_fails_keeps_the_record_for_the_next_run(self):
        """The record is written after the orphans are gone: written first, a failed
        removal lost them from it, and no later run would remove them."""
        conf = self.base / "conf"
        items = [f"file:{conf}/a", f"file:{conf}/b"]
        self.play("present", items)
        recorded = json.loads(self.record.read_text())
        conf.chmod(0o500)  # the files cannot go
        try:
            self.play("absent", [], fails=True)
            self.assertEqual(json.loads(self.record.read_text()), recorded, "the record as it was")
        finally:
            conf.chmod(0o755)
        self.play("absent", [])
        self.assertEqual(sorted(self.removed["file"]), sorted(i.split(":", 1)[1] for i in items))
        self.assertEqual(json.loads(self.record.read_text()), EMPTY)

    def test_absent_removes_an_earlier_versions_unmarked_file_only_in_its_shape(self):
        conf = self.base / "conf"
        old, edited = conf / "old.conf", conf / "edited.conf"
        old.write_text('action(type="omfwd" Target="m" Port="514" Protocol="tcp")\n')
        edited.write_text('action(type="omfwd" Target="m" Port="514" Protocol="tcp")\n*.* @@b:514\n')
        shape = [r'action\(type="omfwd" Target="[^"]+" Port="\d+" Protocol="(tcp|udp)"\)\n?']
        items = [f"file:{old}", f"file:{edited}"]
        legacy = {str(old): shape, str(edited): shape}
        self.play("absent", items, check=True, legacy=legacy)
        self.assertTrue(old.exists(), "--check removes nothing")
        self.play("absent", items, legacy=legacy)
        self.assertEqual(self.removed["file"], [str(old)])
        self.assertTrue(edited.exists(), "a file edited by hand is the site's")
        # present writes and records as before; the shape matters only to absent.
        self.assertGreater(self.play("present", [f"file:{edited}"], legacy={str(edited): shape}), 0)


SOURCE_PLAY = """
- hosts: all
  connection: local
  gather_facts: false
  vars:
    component_record_path: "{{ record }}-{{ inventory_hostname }}"
  tasks:
    - name: Fail a host first, when asked
      ansible.builtin.fail:
      when: inventory_hostname in (fail_hosts | default([]))
    - ansible.builtin.include_role:
        name: component_record
      vars:
        component_record_name: chrony
        component_record_action: present
      tags: [always]
    - name: Notify the restart
      ansible.builtin.debug:
      changed_when: true
      notify: Restart
  handlers:
    - name: Restart
      ansible.builtin.fail:
      when: inventory_hostname in (handler_fails | default([]))
  post_tasks:
    # The play applies deepops_chrony (a component) and sshd (a managed role) whole, as
    # far as the record is concerned: named, never run, so the test changes no host.
    - ansible.builtin.include_role:
        name: component_record
        tasks_from: sources
      vars:
        component_record_sources_also: [deepops_chrony, sshd]
"""


class RunSources(unittest.TestCase):
    """A play's end (tasks_from: sources) records the digests component_source.py gives
    for each host still in the play; --tags or --check keep the ones recorded before, and
    a host that failed, in a task or a handler, or was left out by --limit records none."""

    def play(self, *extra, ok=True):
        argv = ["ansible-playbook", "-i", str(self.config / "inventory"), str(self.playbook),
                "-e", json.dumps({"record": str(self.record)}), *extra]  # fmt: skip
        done = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, env=unrecorded.env(),
                              timeout=unrecorded.TIMEOUT)  # fmt: skip
        if ok:
            self.assertEqual(done.returncode, 0, done.stdout[-3000:] + done.stderr[-2000:])
        return done

    def recorded(self, host="a", name="chrony"):
        path = Path(f"{self.record}-{host}")
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        if name in data["components"]:
            return data["components"][name].get("source")
        return (data.get("managed") or {}).get(name)

    def expected(self, host="a", name="chrony"):
        import component_source

        return component_source.sources(self.config)[host][name]

    def setUp(self):
        import local_scratch

        self.base = local_scratch.directory("record-sources")
        self.addCleanup(local_scratch.remove, self.base)
        self.config = self.base / "config"
        (self.config / "group_vars").mkdir(parents=True)
        (self.config / "inventory").write_text("a\nb\n")
        self.playbook = self.base / "play.yml"
        self.playbook.write_text(SOURCE_PLAY)
        self.record = self.base / "record"

    def set_upstream(self, value):
        (self.config / "group_vars" / "all.yml").write_text(f"chrony_upstream: [{value}]\n")

    def test_only_a_whole_run_records_a_new_digest(self):
        self.set_upstream("ntp.example.invalid")
        self.play()
        first = self.recorded()
        self.assertEqual(first, self.expected())
        self.assertEqual(self.recorded(name="sshd"), self.expected(name="sshd"), "a managed role too")
        self.set_upstream("other.example.invalid")
        self.assertNotEqual(self.expected(), first, "the config value is part of it")
        self.play("--tags", "never-a-tag")
        self.assertEqual(self.recorded(), first, "--tags")
        self.play("--check")
        self.assertEqual(self.recorded(), first, "--check")
        self.play("--limit", "b")
        self.assertEqual(self.recorded(), first, "--limit: a did not run")
        self.assertEqual(self.recorded("b"), self.expected("b"), "--limit: b did")
        self.play()
        self.assertEqual(self.recorded(), self.expected())

    def test_a_host_that_fails_a_task_or_a_handler_records_nothing(self):
        self.set_upstream("ntp.example.invalid")
        self.play()
        first = {h: self.recorded(h) for h in "ab"}
        self.set_upstream("other.example.invalid")
        self.play("-e", json.dumps({"handler_fails": ["b"]}), ok=False)
        self.assertEqual(self.recorded("b"), first["b"], "its restart failed: not deployed")
        self.assertEqual(self.recorded("a"), self.expected("a"), "the other host ran it all")
        self.play("-e", json.dumps({"fail_hosts": ["a"]}), ok=False)
        self.assertEqual(self.recorded("a"), self.expected("a"))
        self.assertEqual(self.recorded("b"), self.expected("b"))
        self.set_upstream("third.example.invalid")
        self.play("-e", json.dumps({"fail_hosts": ["a"]}), ok=False)
        self.assertNotEqual(self.recorded("a"), self.expected("a"), "a task failed: not deployed")

    def readers(self, *units, ok=True):
        """A run whose record is the host's own (deepops_component_record), with UNITS as
        what exports it."""
        extra = {"deepops_component_record": "{{ record }}-{{ inventory_hostname }}",
                 "deepops_component_record_readers": list(units)}  # fmt: skip
        return self.play("-e", json.dumps(extra), ok=ok)

    def test_a_written_record_runs_what_exports_it_and_a_converged_rerun_does_not(self):
        # 2026-11-26: the new digests showed a day late, at the daily measurement
        self.set_upstream("ntp.example.invalid")
        lookup = "textfile_keep : Look up the textfile producers"
        absent = {"name": "deepops-test-not-installed.service", "playbook": "playbooks/x.yml", "on": True}
        self.assertIn(lookup, self.readers(absent).stdout, "written: run")
        self.assertNotIn(lookup, self.readers(absent).stdout, "unchanged: left")
        self.set_upstream("other.example.invalid")  # a new digest alone: written, no change
        self.assertIn(lookup, self.readers(absent).stdout)

    def test_a_reader_on_but_not_installed_warns_with_its_playbook_and_off_is_left(self):
        # 2026-11-27: the record's new reader was on no host yet, and the refresh said nothing
        self.set_upstream("ntp.example.invalid")
        reader = {"name": "deepops-test-not-installed.service", "playbook": "playbooks/x.yml", "on": True}
        done = self.readers(reader)
        out = done.stdout + done.stderr  # a warning goes to stderr
        self.assertIn("deepops-test-not-installed.service is not on a yet", out)
        self.assertIn("run playbooks/x.yml", out)
        self.set_upstream("other.example.invalid")
        done = self.readers({**reader, "on": False})
        self.assertNotIn("is not on a yet", done.stdout + done.stderr, "its role is off here: left")

    def test_a_producer_its_own_role_installs_must_be_there(self):
        self.set_upstream("ntp.example.invalid")
        done = self.readers("deepops-test-not-installed.service", ok=False)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("though the role that runs this installs it", done.stdout)

    def test_a_reader_that_is_not_a_oneshot_fails_the_run(self):
        self.set_upstream("ntp.example.invalid")
        done = self.readers("systemd-journald.service", ok=False)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("systemd-journald.service is not a oneshot that ends", done.stdout)


if __name__ == "__main__":
    unittest.main()
