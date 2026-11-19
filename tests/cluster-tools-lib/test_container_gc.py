"""clustertools.container_gc against temporary stores and a fake /proc.

Each store's path comes from the tool's own configuration; a store is removed when its
user left, when it went unused, or oldest-first while the disk is too full, and never
while its user runs one of the store's programs.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from clustertools import container_gc as gc  # noqa: E402
from clustertools import prom  # noqa: E402

ME = os.getuid()
DAY = 86400


class Paths(unittest.TestCase):
    def test_enroot_reads_conf_d_last_setting_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp, "enroot.conf")
            conf.write_text("#ENROOT_CACHE_PATH ${XDG_CACHE_HOME}/enroot\n")
            Path(tmp, "enroot.conf.d").mkdir()
            Path(tmp, "enroot.conf.d", "10-a.conf").write_text("ENROOT_CACHE_PATH /a/user-$(id -u)\n")
            Path(tmp, "enroot.conf.d", "90-b.conf").write_text("ENROOT_CACHE_PATH /b/user-$(id -u)\n")
            self.assertEqual(gc.enroot_value(conf, "ENROOT_CACHE_PATH"), "/b/user-$(id -u)")

    def test_podman_reads_the_storage_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp, "storage.conf")
            conf.write_text('[storage]\ndriver = "overlay"\nrootless_storage_path = "/s/$UID"  # x\n')
            self.assertEqual(gc.podman_value(conf), "/s/$UID")

    def test_pattern_takes_only_a_uid_in_the_last_component(self):
        parent, regex = gc.pattern("/var/lib/enroot-cache/user-$(id -u)")
        self.assertEqual(parent, "/var/lib/enroot-cache")
        self.assertEqual(regex.match("user-1001").group(1), "1001")
        self.assertIsNone(regex.match("user-1001x"))
        self.assertIsNone(gc.pattern("/home/$USER/cache/$UID"))
        self.assertIsNone(gc.pattern("${HOME}/.cache/enroot"))
        self.assertIsNone(gc.pattern("/var/lib/$UID/storage"))
        self.assertIsNone(gc.pattern(None))


class Collect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.stores = self.root / "stores"
        self.stores.mkdir()
        conf = self.root / "storage.conf"
        conf.write_text(f'[storage]\nrootless_storage_path = "{self.stores}/$UID"\n')
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.cfg = {
            "unused_days": 14,
            "max_used_percent": 100,
            "active_uids": None,
            "uid_min": 0,
            "stores": [{"name": "podman", "kind": "podman", "config": str(conf), "recreate": True,
                        "busy": ["podman", "conmon"]}],
        }  # fmt: skip

    def tearDown(self):
        self.tmp.cleanup()

    def store(self, age_days):
        path = self.stores / str(ME)
        (path / "overlay").mkdir(parents=True)
        (path / "storage.lock").write_text("")
        old = time.time() - age_days * DAY
        for p in (path / "overlay", path / "storage.lock", path):
            os.utime(p, (old, old))
        return path

    def running(self, name):
        d = self.proc / "4242"
        d.mkdir()
        (d / "status").write_text(f"Name:\t{name}\nUid:\t{ME}\t{ME}\t{ME}\t{ME}\n")

    def metrics(self, lines):
        return prom.parse("".join(line + "\n" for line in lines))

    def test_a_recent_store_stays(self):
        path = self.store(2)
        lines, ok = gc.run(self.cfg, proc_root=self.proc)
        self.assertTrue(ok and path.exists() and (path / "storage.lock").exists())
        self.assertEqual(
            self.metrics(lines)[prom.key("container_storage_gc_removed", store="podman", reason="unused")],
            "0",
        )

    def test_an_unused_store_is_emptied_and_recreated_for_its_user(self):
        path = self.store(20)
        lines, ok = gc.run(self.cfg, proc_root=self.proc)
        self.assertTrue(ok)
        self.assertEqual(list(path.iterdir()), [])
        self.assertEqual((path.stat().st_uid, path.stat().st_mode & 0o777), (ME, 0o700))
        self.assertEqual(
            self.metrics(lines)[prom.key("container_storage_gc_removed", store="podman", reason="unused")],
            "1",
        )
        self.assertEqual([p.name for p in self.stores.iterdir()], [str(ME)])  # no trash left

    def test_a_store_in_use_is_never_removed(self):
        path = self.store(20)
        self.running("conmon")
        lines, _ = gc.run(self.cfg, proc_root=self.proc)
        self.assertTrue((path / "storage.lock").exists())
        self.assertEqual(self.metrics(lines)[prom.key("container_storage_gc_busy", store="podman")], "1")

    def test_a_user_who_left_loses_the_store_for_good(self):
        path = self.store(1)
        self.cfg["active_uids"] = [ME + 1]
        lines, _ = gc.run(self.cfg, proc_root=self.proc)
        self.assertFalse(path.exists())
        self.assertEqual(
            self.metrics(lines)[prom.key("container_storage_gc_removed", store="podman", reason="left")], "1"
        )

    def test_a_full_disk_removes_idle_stores_oldest_first(self):
        path = self.store(1)
        self.cfg["max_used_percent"] = 50
        with mock.patch.object(gc, "used_ratio", side_effect=[0.9, 0.4]):
            lines, _ = gc.run(self.cfg, proc_root=self.proc)
        self.assertEqual(list(path.iterdir()), [])
        self.assertEqual(
            self.metrics(lines)[prom.key("container_storage_gc_removed", store="podman", reason="full")], "1"
        )

    def test_a_store_owned_by_someone_else_or_a_symlink_is_left(self):
        other = self.root / "elsewhere"
        other.mkdir()
        (other / "keep").write_text("")
        os.symlink(other, self.stores / str(ME))
        gc.run(self.cfg, proc_root=self.proc)
        self.assertTrue((other / "keep").exists())

    def test_dry_run_changes_nothing(self):
        path = self.store(20)
        gc.run(self.cfg, dry_run=True, proc_root=self.proc)
        self.assertTrue((path / "storage.lock").exists())

    def test_main_writes_the_textfile(self):
        self.store(20)
        out = self.root / "gc.prom"
        self.cfg["textfile"] = str(out)
        cfg = self.root / "gc.json"
        cfg.write_text(json.dumps(self.cfg))
        with mock.patch.object(gc, "processes", return_value={}):
            self.assertEqual(gc.main(["container-storage-gc", str(cfg)]), 0)
        values = prom.parse(out.read_text())
        self.assertIn(prom.key("container_storage_gc_last_success_timestamp_seconds"), values)


if __name__ == "__main__":
    unittest.main()
