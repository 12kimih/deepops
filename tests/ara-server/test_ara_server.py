"""roles/ara_server and the recorder (plugins/callback/ara_default.py): each operator's
server listens on their own unix socket, which the recorder uses only when it is
theirs, and it records over it."""

import contextlib
import os
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import bounds  # noqa: E402
import in_tree  # noqa: E402
from ansible_yaml import role_defaults  # noqa: E402

recorder = in_tree.load("plugins/callback/ara_default.py")
ara_server = in_tree.load("scripts/deepops/ara_server.py")


class Place(unittest.TestCase):
    def test_the_role_serves_where_the_recorder_looks(self):
        defaults = role_defaults("ara_server")
        self.assertEqual(defaults["ara_server_runtime_dir"], "deepops-ara-{{ ara_server_user }}")
        role = defaults["ara_server_socket"].replace(
            "{{ ara_server_runtime_dir }}", defaults["ara_server_runtime_dir"]
        )
        self.assertEqual(role.replace("{{ ara_server_user }}", "{user}"), ara_server.SOCKET)
        self.assertIn("{{ ara_server_user }}", defaults["ara_server_unit"], "one unit per operator")
        self.assertIn("deepops-ara.service", defaults["ara_server_retired_units"], "the TCP one goes")

    def test_the_unit_listens_on_no_tcp_port(self):
        unit = (ROOT / "roles/ara_server/templates/deepops-ara.service.j2").read_text()
        self.assertIn("--bind unix:{{ ara_server_socket }}", unit)
        self.assertIn("RuntimeDirectoryMode=0700", unit)
        self.assertNotRegex(unit, r"--bind [0-9]|ara_server_port")

    def test_a_run_without_the_server_says_how_to_install_it(self):
        note = ara_server.offline_note("/run/x/ara.sock")
        self.assertIn("nothing is lost", note)
        self.assertIn(ara_server.INSTALL, note)
        self.assertTrue((ROOT / ara_server.INSTALL.split()[-1]).is_file(), "the named playbook exists")


class Database(unittest.TestCase):
    def test_the_server_serves_the_roles_base_dir(self):
        self.assertEqual(role_defaults("ara_server")["ara_server_base_dir"].split("}}")[-1], "/.ara/server")
        self.assertEqual(ara_server.BASE_DIR, "~/.ara/server")

    def test_a_run_with_its_own_database_is_never_sent_to_the_server(self):
        self.assertTrue(ara_server.serves_this_run({}))
        self.assertTrue(ara_server.serves_this_run({"ARA_BASE_DIR": "~/.ara/server/"}))
        self.assertFalse(ara_server.serves_this_run({"ARA_BASE_DIR": "/tmp/test-ara"}))
        self.assertFalse(ara_server.serves_this_run({"ARA_DATABASE_NAME": "/tmp/x.sqlite"}))


class Concurrency(unittest.TestCase):
    """ARA 345 (2026-11-26): a read of the history held sqlite's file while a run
    recorded; the result's write failed after 5 s and the result went unrecorded."""

    def test_the_unit_opens_the_database_in_wal_with_immediate_writes(self):
        from ansible_yaml import render

        unit = (ROOT / "roles/ara_server/templates/deepops-ara.service.j2").read_text()
        line = next(x for x in unit.splitlines() if "ARA_DATABASE_OPTIONS" in x)
        options = role_defaults("ara_server")["ara_server_database_options"]
        rendered = render(line, {"ara_server_database_options": options})
        self.assertTrue(
            rendered.startswith("Environment='ARA_DATABASE_OPTIONS=@json ") and rendered.endswith("'")
        )
        prefix = "Environment='"
        self.assertNotIn("'", rendered[len(prefix) : -1], "one quoted assignment for systemd")
        self.assertIn("journal_mode=WAL", options["init_command"])
        self.assertEqual(options["transaction_mode"], "IMMEDIATE")
        self.assertGreaterEqual(options["timeout"], 30)

    def test_ara_takes_the_options_and_the_database_turns_wal(self):
        import json
        import sqlite3
        import subprocess

        options = role_defaults("ara_server")["ara_server_database_options"]
        manage = Path(sys.executable).with_name("ara-manage")
        if not manage.exists():
            self.skipTest("ara-manage is not installed here")
        with tempfile.TemporaryDirectory() as base:
            env = {**os.environ, "ARA_BASE_DIR": base, "ARA_DATABASE_OPTIONS": "@json " + json.dumps(options)}
            done = subprocess.run(
                [str(manage), "migrate"], env=env, capture_output=True, text=True, timeout=bounds.CHILD
            )
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            with contextlib.closing(sqlite3.connect(Path(base) / "ansible.sqlite")) as db:
                self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")


class Hello(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b'{"path": "%s", "host": "%s"}' % (self.path.encode(), self.headers["Host"].encode())
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class UnixHTTPServer(HTTPServer):
    address_family = socket.AF_UNIX

    def server_bind(self):
        self.socket.bind(self.server_address)

    def get_request(self):
        conn, _ = self.socket.accept()
        return conn, ("local", 0)


class Socket(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.run_dir = Path(self.dir.name, "run")
        self.run_dir.mkdir(mode=0o700)
        self.path = str(self.run_dir / "ara.sock")

    def serve(self):
        server = UnixHTTPServer(self.path, Hello)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

    def test_no_socket_is_not_usable(self):
        self.assertEqual(ara_server.usable(self.path), "")
        self.assertIn("No such file", ara_server.owned(self.path))

    def test_ones_own_socket_answering_is_used(self):
        self.serve()
        self.assertEqual(ara_server.owned(self.path), "")
        self.assertEqual(ara_server.usable(self.path, timeout=2), self.path)

    def test_a_socket_of_another_uid_is_refused(self):
        self.serve()
        self.assertIn("not a socket of uid", ara_server.owned(self.path, uid=os.getuid() + 1))
        self.assertEqual(ara_server.usable(self.path, uid=os.getuid() + 1), "")

    def test_a_directory_others_may_enter_is_refused(self):
        self.serve()
        os.chmod(self.run_dir, 0o755)
        self.assertIn("alone", ara_server.owned(self.path))

    def test_a_file_that_is_no_socket_is_refused(self):
        Path(self.path).write_text("")
        self.assertIn("not a socket", ara_server.owned(self.path))

    def test_the_recorder_talks_http_over_the_socket_and_no_proxy(self):
        self.serve()
        with mock.patch.dict(
            os.environ, {"http_proxy": "http://203.0.113.1:9", "HTTP_PROXY": "http://203.0.113.1:9"}
        ):
            got = recorder.unix_client(self.path).get("/api/v1/playbooks", limit=1)
        self.assertEqual(got, {"path": "/api/v1/playbooks?limit=1", "host": "localhost"})

    def test_the_socket_path_names_the_person(self):
        self.assertEqual(ara_server.socket_path(0), "/run/deepops-ara-root/ara.sock")


if __name__ == "__main__":
    unittest.main()
