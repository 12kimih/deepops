"""Exercise actual playbook tasks on scratch storage, with synthetic mount facts.

This proves guards, permissions and idempotence, not NFS behavior. No mounts,
remote hosts, users, packages or services are created by this fixture.
"""

import getpass
import grp
import os
import sys
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import unrecorded  # noqa: E402


@unittest.skipUnless(shutil.which("ansible-playbook"), "ansible-playbook required")
class ModelCachePlaybookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mount = Path(self.temp.name) / "shared"
        self.mount.mkdir()
        self.root = self.mount / "hf-cache"
        self.play = yaml.safe_load((ROOT / "playbooks/model-cache.yml").read_text())[0]
        self.play.update(hosts="all", gather_facts=False, connection="local")
        self.hosts = "localhost ansible_connection=local\n"
        self.site_vars = dict(
            model_cache_mount=str(self.mount),
            model_cache_owner=getpass.getuser(),
            model_cache_group=grp.getgrgid(os.getgid()).gr_name,
            ansible_become=False,
        )
        # Stands in for gathered facts. Play vars, because Ansible sets ansible_facts
        # itself over an inventory variable of that name.
        self.facts = {"mounts": [{"mount": str(self.mount), "fstype": "nfs4"}]}
        # What `exportfs -s` prints on this host: a fake one first in PATH, else none.
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()

    def exports(self, *paths):
        fake = self.bin / "exportfs"
        lines = "".join(f"{p}\t10.0.0.0/8(rw,sync,root_squash,no_subtree_check)\n" for p in paths)
        fake.write_text(f"#!/bin/sh\nprintf '{lines}'\n")
        fake.chmod(0o755)

    def run_play(self, success=True, check=False):
        path = Path(self.temp.name) / "fixture.yml"
        play = {**self.play, "vars": {**self.play.get("vars", {}), "ansible_facts": self.facts}}
        path.write_text(yaml.safe_dump([play]))
        config = Path(self.temp.name) / "ansible.cfg"
        config.write_text(f"[defaults]\nretry_files_enabled = False\nroles_path = {ROOT / 'roles'}\n")
        env = unrecorded.env(
            ANSIBLE_CONFIG=str(config),
            ANSIBLE_NOCOLOR="1",
            ANSIBLE_LOCAL_TEMP=str(Path(self.temp.name) / "ansible-local"),
            ANSIBLE_REMOTE_TEMP=str(Path(self.temp.name) / "ansible-remote"),
            PATH=f"{self.bin}:{os.environ['PATH']}",
        )
        inventory = Path(self.temp.name) / "inventory"
        inventory.mkdir(exist_ok=True)
        (inventory / "hosts").write_text(self.hosts)
        (inventory / "group_vars").mkdir(exist_ok=True)
        (inventory / "group_vars" / "all.yml").write_text(yaml.safe_dump(self.site_vars))
        cmd = ["ansible-playbook", "-i", str(inventory), str(path)]
        if check:
            cmd.append("--check")
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=unrecorded.TIMEOUT)
        self.assertEqual(result.returncode, 0 if success else 2, result.stdout + result.stderr)
        return result.stdout

    def test_permissions_and_idempotence(self):
        self.run_play()
        self.assertEqual(self.root.stat().st_mode & 0o7777, 0o2750)
        self.assertEqual((self.root / "hub").stat().st_mode & 0o7777, 0o2750)
        self.assertIn("changed=0", self.run_play())

    def test_old_environment_file_removed(self):
        # HF_HUB_CACHE is user_environment's (roles/user_environment_defaults) now.
        self.root.mkdir()
        (self.root / "environment.sh").write_text("export HF_HUB_CACHE=/old\n")
        self.run_play()
        self.assertFalse((self.root / "environment.sh").exists())

    def test_every_user_reads_with_mode_2755(self):
        self.site_vars["model_cache_mode"] = "2755"
        self.run_play()
        self.assertEqual((self.root / "hub").stat().st_mode & 0o7777, 0o2755)

    def test_other_mode_rejected(self):
        self.site_vars["model_cache_mode"] = "2777"
        self.run_play(success=False)
        self.assertFalse(self.root.exists())

    def test_nfs_server_writes_its_own_export(self):
        self.facts["mounts"][0]["fstype"] = "ext4"
        self.exports(str(self.mount))
        self.run_play()
        self.assertTrue((self.root / "hub").is_dir())

    def test_local_filesystem_exporting_another_path_fails(self):
        self.facts["mounts"][0]["fstype"] = "ext4"
        self.exports(str(self.mount) + "-other")
        self.run_play(success=False)
        self.assertFalse(self.root.exists())

    def test_cache_below_a_subdirectory_of_the_mount(self):
        custom = self.mount / "shared" / "models"
        self.site_vars["model_cache_root"] = str(custom)
        (self.mount / "shared").mkdir()
        self.run_play()
        self.assertTrue((custom / "hub").is_dir())

    def test_multiple_writers_rejected_before_cache_creation(self):
        self.hosts += "second-writer ansible_connection=local\n"
        self.run_play(success=False)
        self.assertFalse(self.root.exists())

    def test_absent_mount_fails_before_creating_cache(self):
        self.facts["mounts"] = []
        self.run_play(success=False)
        self.assertFalse(self.root.exists())

    def test_local_filesystem_fails(self):
        self.facts["mounts"][0]["fstype"] = "ext4"
        self.run_play(success=False)
        self.assertFalse(self.root.exists())

    def test_cache_cannot_be_mount_root(self):
        self.site_vars["model_cache_root"] = str(self.mount)
        self.run_play(success=False)
        self.assertFalse((self.mount / "hub").exists())

    def test_cache_cannot_escape_mount(self):
        self.site_vars["model_cache_root"] = str(self.mount) + "/../escape"
        self.run_play(success=False)
        self.assertFalse((self.mount.parent / "escape").exists())

    def test_symlink_cache_rejected(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        self.root.symlink_to(outside)
        self.run_play(success=False)
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlink_hub_rejected(self):
        self.root.mkdir()
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (self.root / "hub").symlink_to(outside)
        self.run_play(success=False)
        self.assertEqual(list(outside.iterdir()), [])

    def test_inventory_can_choose_dedicated_cache_child(self):
        custom = self.mount / "models"
        self.site_vars["model_cache_root"] = str(custom)
        self.run_play()
        self.assertTrue((custom / "hub").is_dir())
        self.assertFalse(self.root.exists())

    def test_check_mode_has_no_cache_side_effects(self):
        self.run_play(check=True)
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
