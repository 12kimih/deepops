"""grafana_reconcile against an in-process fake of the Grafana API it uses.

The fake mimics file provisioning: a provisioned dashboard cannot be deleted through the
API, reload deletes dashboards whose file is gone and provisions files into the
provider's folder. (Behaviour checked against Grafana 13.)
"""

import importlib.util
import json
import os
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("reconcile", ROOT / "roles/grafana/files/grafana_reconcile.py")
reconcile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reconcile)


class FakeGrafana:
    def __init__(self, path, provider_folder):
        self.path = path
        self.provider_folder = provider_folder
        self.folders = {}  # uid -> title
        self.dashboards = {}  # uid -> {folder, provisioned: relpath or None}

    def provision(self):
        for uid, d in list(self.dashboards.items()):
            if d["provisioned"] and not os.path.exists(os.path.join(self.path, d["provisioned"])):
                del self.dashboards[uid]
        for dirpath, _, files in os.walk(self.path):
            for name in files:
                if not name.endswith(".json"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, name), self.path)
                uid = json.load(open(os.path.join(dirpath, name)))["uid"]
                if uid not in self.dashboards:
                    self.folders.setdefault(self.provider_folder, "provider")
                    self.dashboards[uid] = {"folder": self.provider_folder, "provisioned": rel}

    def handle(self, method, url, body):
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query)
        parts = u.path.strip("/").split("/")
        if parts[:2] == ["api", "folders"]:
            if method == "POST":
                self.folders[body["uid"]] = body["title"]
                return 200, {}
            uid = parts[2]
            if uid not in self.folders:
                return 404, {"message": "not found"}
            if method == "GET":
                return 200, {"uid": uid, "title": self.folders[uid]}
            if method == "PUT":
                self.folders[uid] = body["title"]
                return 200, {}
            if method == "DELETE":
                del self.folders[uid]
                return 200, {}
        if parts[:3] == ["api", "dashboards", "uid"]:
            d = self.dashboards.get(parts[3])
            if d is None:
                return 404, {"message": "not found"}
            if method == "DELETE":
                if d["provisioned"]:
                    return 400, {"message": "provisioned dashboard cannot be deleted"}
                del self.dashboards[parts[3]]
                return 200, {}
            meta = {
                "provisioned": bool(d["provisioned"]),
                "provisionedExternalId": d["provisioned"] or "",
                "folderUid": d["folder"],
                "folderTitle": self.folders.get(d["folder"], "General"),
            }
            return 200, {"meta": meta}
        if u.path == "/api/search":
            if q.get("type") == ["dash-folder"]:
                return 200, [{"uid": k, "title": t} for k, t in self.folders.items() if q["query"][0] in t]
            if "folderUIDs" in q:
                return 200, [
                    {"uid": k} for k, d in self.dashboards.items() if d["folder"] == q["folderUIDs"][0]
                ]
            return 200, [{"uid": k} for k in self.dashboards]
        if u.path == "/api/admin/provisioning/dashboards/reload":
            self.provision()
            return 200, {}
        return 500, {"message": f"unexpected {method} {url}"}


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, "deepops"))
        with open(os.path.join(self.tmp.name, "deepops/gpu-nodes.json"), "w") as f:
            json.dump({"uid": "gpu-nodes"}, f)
        # The live state that motivated the script: the old folder holds an orphaned
        # dashboard and a provisioned one, the provider now names a new folder.
        self.g = FakeGrafana(self.tmp.name, "new")
        self.g.folders = {"old": "Cluster", "new": "cluster-a"}
        self.g.dashboards = {
            "syl6zhqkz": {"folder": "old", "provisioned": None},
            "gpu-nodes": {"folder": "old", "provisioned": "deepops/gpu-nodes.json"},
            "mine": {"folder": "", "provisioned": None},
        }
        fake = self.g

        class Handler(BaseHTTPRequestHandler):
            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                ok = self.headers.get("Authorization") == reconcile.Grafana("", "admin", "pw").auth
                status, out = fake.handle(self.command, self.path, body) if ok else (401, {"message": "auth"})
                raw = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_PUT = do_DELETE = _do

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.api = reconcile.Grafana(f"http://127.0.0.1:{self.server.server_port}", "admin", "pw")

    def run_once(self, title="cluster-a", check=False, retired=("syl6zhqkz", "rYdddlPWk")):
        return reconcile.reconcile(self.api, "new", title, self.tmp.name, list(retired), check)

    def test_converges_then_is_idempotent(self):
        actions = self.run_once()
        self.assertEqual(len(actions), 3, actions)
        self.assertEqual(self.g.folders, {"new": "cluster-a"})
        self.assertEqual(set(self.g.dashboards), {"gpu-nodes", "mine"})
        self.assertEqual(self.g.dashboards["gpu-nodes"]["folder"], "new")
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "deepops/gpu-nodes.json")))
        self.assertEqual(self.run_once(), [])

    def test_check_changes_nothing(self):
        before = (dict(self.g.folders), json.dumps(self.g.dashboards, sort_keys=True))
        self.assertTrue(self.run_once(check=True))
        self.assertEqual(before, (dict(self.g.folders), json.dumps(self.g.dashboards, sort_keys=True)))

    def test_renames_folder_in_place(self):
        self.run_once()
        self.assertEqual(self.run_once(title="renamed"), ["rename folder 'cluster-a' to 'renamed' (new)"])
        self.assertEqual(self.g.folders, {"new": "renamed"})

    def test_removes_an_empty_folder_of_the_same_title(self):
        self.g.folders["dup"] = "cluster-a"
        self.run_once()
        self.assertNotIn("dup", self.g.folders)

    def test_creates_missing_folder(self):
        self.g.folders.pop("new")
        self.run_once()
        self.assertEqual(self.g.folders["new"], "cluster-a")

    def test_keeps_a_folder_with_other_dashboards(self):
        self.g.dashboards["users-own"] = {"folder": "old", "provisioned": None}
        self.run_once()
        self.assertIn("old", self.g.folders)
        self.assertIn("users-own", self.g.dashboards)

    def test_never_deletes_a_provisioned_retired_uid(self):
        actions = self.run_once(retired=("gpu-nodes",))
        self.assertIn("gpu-nodes", self.g.dashboards)
        self.assertTrue(any(a.startswith("keep retired dashboard gpu-nodes") for a in actions))

    def test_main_reports_auth_failure(self):
        import io
        import sys

        argv = [
            "--url",
            self.api.url,
            "--folder-uid",
            "new",
            "--folder-title",
            "t",
            "--dashboards-path",
            self.tmp.name,
            "--password-stdin",
        ]
        stdin, stderr = sys.stdin, sys.stderr
        sys.stdin, sys.stderr = io.StringIO("wrong\n"), io.StringIO()
        try:
            self.assertEqual(reconcile.main(argv), 1)
            self.assertIn("HTTP 401", sys.stderr.getvalue())
        finally:
            sys.stdin, sys.stderr = stdin, stderr


if __name__ == "__main__":
    unittest.main()
