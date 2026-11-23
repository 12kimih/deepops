"""cluster_tools.shared_data: public https sources only (every redirect too), a download
kept as published within what was asked and the budget (downloads still running
count), checked by its sha256, started as the writer; the ledger records it and the uses
people report; the usage is the budget's project series."""

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from cluster_tools import bot_serve, prom, shared_data  # noqa: E402

PUBLIC = "93.184.215.14"  # a global address (any would do: the resolver is faked)


def resolver(table):
    def resolve(host, port, proto=0):
        return [(2, 1, 6, "", (a, port)) for a in table.get(host, [PUBLIC])]

    return resolve


class Page(io.BytesIO):
    def __init__(self, body, url):
        super().__init__(body)
        self.url = url

    def geturl(self):
        return self.url


class SharedData(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.join(tmp.name, "shared")
        for kind in ("datasets", "models"):
            os.makedirs(os.path.join(self.root, kind))
        self.cfg = {"root": self.root, "kinds": ["datasets", "models"], "writer": "shared",
                    "budget": 10 << 20, "warn_fraction": 0.9, "ledger": os.path.join(tmp.name, "ledger.json"),
                    "hosts": [], "command": "/usr/local/sbin/cluster-shared-data", "config": "/etc/c.json",
                    "project": "shared-data", "usage_metrics": True,
                    "textfile": os.path.join(tmp.name, "prom", "shared-data.prom"), "textfile_kept": ""}  # fmt: skip
        self.started = []
        self.resolve = resolver({"intra.example": ["10.1.2.3"], "dual.example": [PUBLIC, "127.0.0.1"]})

    def fetch(self, name, size=1 << 20, sha="-", url="https://data.example/set.tar"):
        return shared_data.fetch(self.cfg, "datasets", name, url, sha, str(size), "alice", 1000,
                                 start=self.started.append, resolve=self.resolve)  # fmt: skip

    def test_only_public_https_sources(self):
        ok = shared_data.public_url("https://data.example/a.tar", (), self.resolve)
        self.assertEqual(ok, "https://data.example/a.tar")
        for url in ("http://data.example/a.tar", "file:///etc/shadow", "https://u:p@data.example/a",
                    "https://intra.example/a", "https://dual.example/a", "ftp://data.example/a"):  # fmt: skip
            with self.subTest(url), self.assertRaises(shared_data.Error):
                shared_data.public_url(url, (), self.resolve)
        hosts = ("huggingface.co",)
        shared_data.public_url("https://cdn.huggingface.co/x", hosts, self.resolve)
        with self.assertRaises(shared_data.Error):
            shared_data.public_url("https://huggingface.co.evil.example/x", hosts, self.resolve)

    def test_a_fetch_is_recorded_and_runs_as_the_writer(self):
        self.assertEqual(self.fetch("imagenet")["started"], "shared-data-datasets-imagenet")
        argv = self.started[0]
        self.assertEqual(argv[0], "systemd-run")
        self.assertIn("--uid=shared", argv)
        self.assertEqual(argv[-3:], ["download", "datasets", "imagenet"])
        entry = shared_data.read_ledger(self.cfg)["datasets/imagenet"]
        self.assertEqual(
            (entry["state"], entry["requester"], entry["max_bytes"]), ("fetching", "alice", 1 << 20)
        )

    def test_refused_names_duplicates_and_what_overruns_the_budget(self):
        for kind, name in (
            ("datasets", "../etc"),
            ("datasets", ".hidden"),
            ("secrets", "x"),
            ("datasets", ""),
        ):
            with self.subTest(name=name), self.assertRaises(shared_data.Error):
                shared_data.fetch(self.cfg, kind, name, "https://data.example/a", "-", "1", "alice", 0,
                                  start=self.started.append, resolve=self.resolve)  # fmt: skip
        self.fetch("one", size=6 << 20)
        with self.assertRaises(shared_data.Error):
            self.fetch("one")
        # 6M still downloading plus 6M asked overruns 10M, though the disk holds nothing yet.
        with self.assertRaises(shared_data.Error) as e:
            self.fetch("two", size=6 << 20)
        self.assertIn("coming", str(e.exception))
        self.assertEqual(len(self.started), 1)

    def test_the_download_lands_whole_checked_or_not_at_all(self):
        body = b"x" * 3000
        good = hashlib.sha256(body).hexdigest()
        self.fetch("ok", sha=good)
        opened = lambda req, timeout: Page(body, "https://data.example/set.tar")  # noqa: E731
        out = shared_data.download(
            self.cfg, "datasets", "ok", now=lambda: 2000, opener=opened, resolve=self.resolve
        )
        self.assertEqual(out["bytes"], 3000)
        self.assertEqual(Path(self.root, "datasets/ok/set.tar").read_bytes(), body)
        entry = shared_data.read_ledger(self.cfg)["datasets/ok"]
        self.assertEqual((entry["state"], entry["file"], entry["bytes"]), ("done", "set.tar", 3000))
        self.fetch("bad", sha="0" * 64)
        with self.assertRaises(shared_data.Error):
            shared_data.download(
                self.cfg, "datasets", "bad", now=lambda: 2000, opener=opened, resolve=self.resolve
            )
        self.fetch("big", size=1000)
        with self.assertRaises(shared_data.Error):
            shared_data.download(
                self.cfg, "datasets", "big", now=lambda: 2000, opener=opened, resolve=self.resolve
            )
        ledger = shared_data.read_ledger(self.cfg)
        self.assertEqual(
            (ledger["datasets/bad"]["state"], ledger["datasets/big"]["state"]), ("failed", "failed")
        )
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "datasets"))), ["ok"])
        # A failed entry may be asked for again.
        self.fetch("bad")

    def test_uses_are_recorded_and_a_removal_takes_the_directory(self):
        self.fetch("ok")
        with self.assertRaises(shared_data.Error):  # not there yet
            shared_data.use(self.cfg, "datasets", "ok", "bob", 1500)
        page = lambda req, timeout: Page(b"y", "https://data.example/set.tar")  # noqa: E731
        shared_data.download(self.cfg, "datasets", "ok", now=lambda: 2000, opener=page, resolve=self.resolve)
        shared_data.use(self.cfg, "datasets", "ok", "bob", 3000)
        self.assertEqual(shared_data.read_ledger(self.cfg)["datasets/ok"]["uses"], {"bob": 3000})
        self.assertEqual(
            shared_data.remove(self.cfg, "datasets", "ok"), {"removed": "datasets/ok", "recorded": True}
        )
        self.assertFalse(os.path.lexists(os.path.join(self.root, "datasets/ok")))
        self.assertEqual(shared_data.read_ledger(self.cfg), {})

    def test_the_bot_may_ask_for_these_four_only(self):
        self.assertIn("entries", shared_data.serve(self.cfg, "list", 0))
        for request in ("download datasets x", "remove datasets", "metrics", "list extra", ""):
            with self.subTest(request), self.assertRaises(bot_serve.Error):
                shared_data.serve(self.cfg, request, 0)

    def test_the_usage_is_the_budgets_project_series(self):
        Path(self.root, "models", "w.bin").write_bytes(b"z" * 8192)
        shared_data.metrics(self.cfg, 5000)
        values = prom.parse(Path(self.cfg["textfile"]).read_text())
        series = {(name, dict(labels).get("kind")): float(v) for (name, labels), v in values.items()}
        self.assertGreaterEqual(series[("disk_quota_project_used_bytes", None)], 8192)
        self.assertEqual(series[("disk_quota_project_limit_bytes", "hard")], 10 << 20)
        self.assertEqual(series[("disk_quota_project_limit_bytes", "warn")], int((10 << 20) * 0.9))
        project = {dict(labels).get("project") for (name, labels) in values if name.startswith("disk_quota")}
        self.assertEqual(project, {"shared-data"})
        self.cfg["usage_metrics"] = False  # project quotas on: disk_quota exports it
        shared_data.metrics(self.cfg, 5000)
        self.assertNotIn("disk_quota", Path(self.cfg["textfile"]).read_text())

    def test_the_config_the_role_writes_is_checked(self):
        path = Path(self.cfg["ledger"]).with_name("c.json")
        path.write_text(json.dumps(self.cfg))
        self.assertEqual(shared_data.load_config(str(path))["budget"], 10 << 20)
        path.write_text(json.dumps({**self.cfg, "kinds": ["../x"]}))
        with self.assertRaises(ValueError):
            shared_data.load_config(str(path))


if __name__ == "__main__":
    unittest.main()
