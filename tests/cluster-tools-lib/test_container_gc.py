"""cluster_tools.container_gc against temporary stores and a fake /proc.

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
from cluster_tools import container_gc as gc  # noqa: E402
from cluster_tools import prom  # noqa: E402

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

    def test_a_store_only_read_is_in_use(self):
        # enroot imports again from its cached layers without writing them.
        path = self.store(20)
        os.utime(path / "storage.lock", (time.time(), time.time() - 20 * DAY))
        gc.run(self.cfg, proc_root=self.proc)
        self.assertTrue((path / "storage.lock").exists())

    def test_a_users_trash_lookalike_neither_blocks_nor_is_taken(self):
        # The parent is writable by users (1777): a name they can guess could block the
        # rename, and a .gc-* directory of theirs was deleted as a leftover.
        path = self.store(20)
        theirs = self.stores / ".gc-mine"
        theirs.mkdir(mode=0o755)
        (theirs / "keep").write_text("")
        lines, ok = gc.run(self.cfg, proc_root=self.proc)
        self.assertTrue(ok)
        self.assertEqual(list(path.iterdir()), [])
        self.assertTrue((theirs / "keep").exists())
        self.assertEqual(sorted(p.name for p in self.stores.iterdir()), [".gc-mine", str(ME)])

    def test_one_store_that_cannot_move_does_not_stop_the_run(self):
        path = self.store(20)
        with mock.patch.object(gc.os, "rename", side_effect=PermissionError("EPERM")):
            lines, ok = gc.run(self.cfg, proc_root=self.proc)
        self.assertFalse(ok)
        self.assertTrue((path / "storage.lock").exists())
        self.assertEqual(
            self.metrics(lines)[prom.key("container_storage_gc_removed", store="podman", reason="unused")],
            "0",
        )

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
        out, kept = self.root / "gc.prom", self.root / "keep" / "gc.prom"
        self.cfg.update(textfile=str(out), textfile_kept=str(kept))
        cfg = self.root / "gc.json"
        cfg.write_text(json.dumps(self.cfg))
        with mock.patch.object(gc, "processes", return_value={}):
            self.assertEqual(gc.main(["container-storage-gc", str(cfg)]), 0)
        values = prom.parse(out.read_text())
        self.assertIn(prom.key("container_storage_gc_last_success_timestamp_seconds"), values)
        self.assertEqual(kept.read_text(), out.read_text(), "the copy roles/textfile_keep restores at boot")

    def test_a_failed_run_after_a_reboot_keeps_the_last_success_from_the_kept_copy(self):
        # The textfile directory is a tmpfs: after a boot only the kept copy holds the last
        # success, and a failed run must carry it over, or the staleness alert never fires.
        out, kept = self.root / "gc.prom", self.root / "keep" / "gc.prom"
        kept.parent.mkdir()
        kept.write_text("container_storage_gc_last_success_timestamp_seconds 1700000000\n")
        self.cfg.update(textfile=str(out), textfile_kept=str(kept))
        cfg = self.root / "gc.json"
        cfg.write_text(json.dumps(self.cfg))
        with mock.patch.object(gc, "run", return_value=(["container_storage_gc_failures 1"], False)):
            self.assertEqual(gc.main(["container-storage-gc", str(cfg)]), 1)
        self.assertIn("container_storage_gc_last_success_timestamp_seconds 1700000000", out.read_text())


class FakeDockerd:
    """A unix socket that answers GET /containers/json?all=1 with CONTAINERS, as dockerd."""

    def __init__(self, path, containers, status=200):
        import socketserver
        import threading
        from http.server import BaseHTTPRequestHandler

        body = json.dumps(containers).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.server.paths.append(self.path)
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        class Server(socketserver.UnixStreamServer):
            def get_request(self):
                request, _ = super().get_request()
                return request, ("local", 0)

        self.server = Server(str(path), Handler)
        self.server.paths = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class UnmanagedContainers(unittest.TestCase):
    """Containers deepops did not start are counted, never touched."""

    LABEL = "deepops.unit"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = time.time()
        self.containers = [
            {"State": "running", "Created": self.now - 30 * DAY, "Labels": {self.LABEL: "docker.node-exporter.service"}},
            {"State": "running", "Created": self.now - 30 * DAY, "Labels": {"maintainer": "x"}},
            {"State": "running", "Created": self.now - DAY, "Labels": None},
            {"State": "exited", "Created": self.now - 120 * DAY, "Labels": {}},
        ]  # fmt: skip

    def counts(self, lines):
        values = prom.parse("".join(line + "\n" for line in lines))
        return {k: int(v) for k, v in values.items()}

    def test_the_label_marks_deepops_and_age_marks_stale(self):
        got = self.counts(gc.unmanaged(self.containers, self.LABEL, self.now - 14 * DAY))
        self.assertEqual(got[prom.key("container_storage_gc_docker_unmanaged", state="running")], 2)
        self.assertEqual(got[prom.key("container_storage_gc_docker_unmanaged_stale", state="running")], 1)
        self.assertEqual(got[prom.key("container_storage_gc_docker_unmanaged", state="stopped")], 1)
        self.assertEqual(got[prom.key("container_storage_gc_docker_unmanaged_stale", state="stopped")], 1)

    def test_the_engine_api_is_read_over_dockerds_socket(self):
        sock = Path(self.tmp.name, "docker.sock")
        dockerd = FakeDockerd(sock, self.containers)
        self.addCleanup(dockerd.close)
        self.assertEqual(gc.docker_containers(str(sock)), self.containers)
        self.assertEqual(dockerd.server.paths, ["/containers/json?all=1"])

    def test_no_dockerd_means_no_count_and_the_run_still_succeeds(self):
        cfg = {"unused_days": 14, "max_used_percent": 100, "active_uids": None, "stores": [],
               "docker": {"socket": str(Path(self.tmp.name, "missing.sock")), "label": self.LABEL}}  # fmt: skip
        lines, ok = gc.run(cfg, now=self.now)
        self.assertTrue(ok)
        self.assertEqual([line for line in lines if "docker" in line], [])

    def test_an_api_error_is_logged_not_fatal(self):
        sock = Path(self.tmp.name, "docker.sock")
        dockerd = FakeDockerd(sock, {"message": "no"}, status=500)
        self.addCleanup(dockerd.close)
        cfg = {"unused_days": 14, "max_used_percent": 100, "active_uids": None, "stores": [],
               "docker": {"socket": str(sock), "label": self.LABEL}}  # fmt: skip
        lines, ok = gc.run(cfg, now=self.now)
        self.assertTrue(ok)
        self.assertEqual([line for line in lines if "docker" in line], [])

    def test_a_config_from_before_the_kept_copy_still_runs(self):
        # textfile_kept came later (8a9387b5): an older config lacks it and writes no kept
        # copy, as it did; requiring it refused every host's config (ARA 262).
        out = Path(self.tmp.name, "gc.prom")
        old = {
            "textfile": str(out),
            "unused_days": 14,
            "max_used_percent": 80,
            "active_uids": None,
            "stores": [],
        }
        path = Path(self.tmp.name, "gc.json")
        path.write_text(json.dumps(old))
        self.assertEqual(gc.main(["container-storage-gc", str(path)]), 0)
        self.assertIn("container_storage_gc_last_success", out.read_text())

    def test_the_config_takes_exactly_socket_and_label(self):
        base = {
            "textfile": "",
            "textfile_kept": "",
            "unused_days": 14,
            "max_used_percent": 80,
            "active_uids": None,
            "stores": [],
        }
        for docker, good in (({"socket": "/run/docker.sock", "label": "deepops.unit"}, True),
                             ({"socket": "/run/docker.sock"}, False), ({"socket": "", "label": "x"}, False),
                             ({"socket": "/s", "label": "x", "extra": 1}, False)):  # fmt: skip
            path = Path(self.tmp.name, "gc.json")
            path.write_text(json.dumps({**base, "docker": docker}))
            with self.subTest(docker=docker):
                if good:
                    self.assertEqual(gc.load_config(path)["docker"], docker)
                else:
                    with self.assertRaises(ValueError):
                        gc.load_config(path)


if __name__ == "__main__":
    unittest.main()
