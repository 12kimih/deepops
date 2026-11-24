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


class Port(unittest.TestCase):
    def test_the_role_serves_where_the_recorder_looks(self):
        # The recorder probes ARA's default api_server; the role must bind exactly there.
        import yaml

        doc = yaml.safe_load(recorder.DOCUMENTATION)
        default = doc["options"]["api_server"]["default"]
        defaults = role_defaults("ara_server")
        self.assertEqual(default, f"http://{defaults['ara_server_bind']}:{defaults['ara_server_port']}")


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
