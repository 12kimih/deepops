"""settings.py: reading variable files, owners, Ansible's precedence, redundancy, diff."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import settings as S  # noqa: E402

ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def write(base, files):
    for rel, text in files.items():
        path = Path(base) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def git(base, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(base), *args],
        check=True, capture_output=True, env=ENV,
    )  # fmt: skip


def owned(**values):
    return {k: S.Owner([f"roles/r/defaults/main.yml#{k}"], v) for k, v in values.items()}


class Entries(unittest.TestCase):
    TEXT = """---
# A comment, not a setting.
# prose: with one space is prose
active: 1
listed:
  - a
  - b
#shown: 2
#block:
#  - x
#  # a comment inside the block
#  - y
#sample: 3  # example
# site: why this site sets it
pinned: true
#broken: [unclosed
"""

    def test_active_commented_example_and_pinned(self):
        found = {e.name: e for e in S.entries(self.TEXT)}
        self.assertEqual(sorted(found), ["active", "block", "broken", "listed", "pinned", "sample", "shown"])
        self.assertEqual(
            (found["listed"].active, found["listed"].value, found["listed"].end), (True, ["a", "b"], 7)
        )
        self.assertEqual((found["shown"].active, found["shown"].value), (False, 2))
        self.assertEqual(found["block"].value, ["x", "y"])
        self.assertTrue(found["sample"].example and not found["shown"].example)
        self.assertTrue(found["pinned"].pinned and not found["active"].pinned)
        self.assertIs(found["broken"].value, S.MISSING)

    def test_a_vaulted_value_stays_opaque(self):
        (entry,) = S.entries("secret: !vault |\n  $ANSIBLE_VAULT;1.1;AES256\n  6162\n")
        self.assertIsInstance(entry.value, S.Vault)
        self.assertNotEqual(entry.value, "6162")


class Resolution(unittest.TestCase):
    """Ansible's order for inventory variables: the owner's default, the inventory
    file's group vars, group_vars/all, group_vars of each group by depth, the inventory
    file's host vars, host_vars."""

    def config(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        write(tmp.name, files)
        return S.Config(S.Tree(tmp.name, tracked_only=False))

    INVENTORY = "[parent:children]\nchild\n[child]\nh1 x_inv_host=1\nh2\n[all:vars]\nx_inv_all=1\n"

    def test_precedence(self):
        c = self.config({
            "inventory": self.INVENTORY,
            "group_vars/all.yml": "a: all\nb: all\nc: all\nx_inv_all: file\n",
            "group_vars/parent.yml": "b: parent\nc: parent\n",
            "group_vars/child.yml": "c: child\n",
            "host_vars/h1": "d: host\n",
        })  # fmt: skip
        got = {k: v for k, (v, _) in c.resolve("h1", owned(a="default", d="default", e="default")).items()}
        self.assertEqual(
            {k: got[k] for k in "abcde"},
            {"a": "all", "b": "parent", "c": "child", "d": "host", "e": "default"},
        )
        self.assertEqual(got["x_inv_all"], "file", "group_vars/all beats the inventory file's [all:vars]")
        self.assertEqual(got["x_inv_host"], 1)
        self.assertNotIn("inventory_dir", got)

    def test_dashed_group_names_keep_their_children(self):
        c = self.config({"inventory": "[slurm-node]\nh1\n[slurm-cluster:children]\nslurm-node\n"})
        self.assertEqual(c.hosts["h1"], ["all", "slurm-cluster", "slurm-node"])

    def test_an_inventory_that_does_not_parse_is_an_error(self):
        with self.assertRaises(Exception):
            self.config({"inventory": "[a:children]\nmissing\n[b\n"})

    def test_redundant_and_unused(self):
        c = self.config({
            "inventory": "[child]\nh1\nh2\n[other]\nh3\n",
            # same: equals the default; kept: all.yml changes it, child restores it there
            "group_vars/all.yml": "same: 1\nkept: 2\n",
            "group_vars/child.yml": "kept: 1\n",
            # other.yml's line loses to h3's own on its only host
            "group_vars/other.yml": "shadowed: 3\n",
            "host_vars/h3": "shadowed: 4\n",
            "group_vars/lonely.yml": "alone: 5\n",
        })  # fmt: skip
        same, unused = S.redundant(c, owned(same=1, kept=1, shadowed=0, alone=5))
        self.assertEqual(sorted((s.rel, e.name) for s, e in same),
                         [("group_vars/all.yml", "same"), ("group_vars/lonely.yml", "alone")])  # fmt: skip
        self.assertEqual([(s.rel, e.name) for s, e in unused], [("group_vars/other.yml", "shadowed")])


class Revisions(unittest.TestCase):
    def test_a_tree_reads_a_revision_and_the_working_tree(self):
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q")
            write(d, {"roles/a/defaults/main.yml": "x: 1\n", "roles/b/defaults/main.yml": "x: 2\ny: 1\n"})
            git(d, "add", "-A")
            git(d, "commit", "-qm", "c")
            write(d, {"roles/b/defaults/main.yml": "x: 1\ny: 1\n"})
            old, new = S.owners(S.Tree(d, rev="HEAD")), S.owners(S.Tree(d))
            self.assertIs(old["x"].value, S.AMBIGUOUS)
            self.assertEqual((new["x"].value, new["x"].roles), (1, {"a", "b"}))

    def test_changes_between_two_states(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, {"inventory": "h1\n", "group_vars/all.yml": "a: 1\n"})
            config = S.Config(S.Tree(d, tracked_only=False))
            found = S.changes(config, owned(a=0, b=1, c=1), config, owned(a=0, b=2, c=1))
            self.assertEqual(found, {"h1": [("b", 1, 2, "roles/r/defaults/main.yml#b")]})


class DeployRecords(unittest.TestCase):
    """Each host carries what was deployed to it (roles/deploy_record); the preview reads
    it back from the controller's fact cache through an ad-hoc debug task."""

    def run_with(self, hosts_json):
        out = json.dumps({"plays": [{"tasks": [{"hosts": hosts_json}]}]})
        done = subprocess.CompletedProcess([], 0, stdout=out, stderr="")
        with mock.patch.object(S.subprocess, "run", return_value=done) as run:
            found = S.deployed("cfg", ["h1", "h2", "h3"])
        argv = run.call_args.args[0]
        self.assertEqual(argv[-4:], ["-m", "ansible.builtin.debug", "-a", "var=ansible_local.deepops_deploy"])
        return found

    def test_the_latest_record_of_each_host(self):
        old = {"deepops": "a", "config": "x", "time": "2026-01-01T00:00:00Z"}
        new = {"deepops": "b", "config": "y", "time": "2026-02-01T00:00:00Z"}
        found = self.run_with({
            "h1": {"ansible_local.deepops_deploy": {"slurm-cluster": old, "k8s-cluster": new}},
            "h2": {"ansible_local.deepops_deploy": "VARIABLE IS NOT DEFINED!"},
        })  # fmt: skip
        self.assertEqual(found, {"h1": new})

    def test_unreadable_output_is_no_record(self):
        done = subprocess.CompletedProcess([], 2, stdout="", stderr="boom")
        with mock.patch.object(S.subprocess, "run", return_value=done):
            self.assertEqual(S.deployed("cfg", ["h1"]), {})

    def test_every_deploy_ends_by_recording(self):
        for name in ("slurm-cluster", "k8s-cluster"):
            last = S.load((ROOT / "playbooks" / f"{name}.yml").read_text())[-1]
            self.assertEqual(last.get("roles"), [{"role": "deploy_record", "deploy_record_name": name}], name)
            self.assertIn("always", last.get("tags", []), f"{name}: a run with --tags records too")


class Owners(unittest.TestCase):
    def test_this_repository(self):
        found = S.owners()
        self.assertEqual(found["slurm_install_nhc"].roles, {"deploy_defaults"})
        self.assertEqual(found["container_manager"].roles, {"kubespray_defaults"})
        self.assertNotIn("artifacts_dir", found, "NOT_OURS has no default for our code")
        self.assertGreater(len(found), 1500)


if __name__ == "__main__":
    unittest.main()
