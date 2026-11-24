"""roles/ara_server and the recorder (plugins/callback/ara_default.py): the role serves
where the recorder looks, and the recorder tells a server that answers from none."""

import socket
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402
from ansible_yaml import role_defaults  # noqa: E402

recorder = in_tree.load("plugins/callback/ara_default.py")
ara_server = in_tree.load("scripts/deepops/ara_server.py")


class Port(unittest.TestCase):
    def test_the_role_serves_where_the_recorder_looks(self):
        # The recorder probes ARA's default api_server; the role must bind exactly there.
        import yaml

        doc = yaml.safe_load(recorder.DOCUMENTATION)
        default = doc["options"]["api_server"]["default"]
        defaults = role_defaults("ara_server")
        self.assertEqual(default, f"http://{defaults['ara_server_bind']}:{defaults['ara_server_port']}")
        self.assertEqual(default, ara_server.ADDRESS, "the doctor probes the same address")

    def test_a_run_without_the_server_says_how_to_install_it(self):
        note = ara_server.offline_note()
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


class Fallback(unittest.TestCase):
    def test_a_closed_port_does_not_answer(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]  # bound, never listening: refused
            self.assertFalse(recorder.server_answers(f"http://127.0.0.1:{port}", timeout=0.5))

    def test_a_listening_port_answers(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            self.assertTrue(recorder.server_answers(f"http://127.0.0.1:{s.getsockname()[1]}", timeout=0.5))


if __name__ == "__main__":
    unittest.main()
