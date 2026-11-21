"""roles/nfs/files/nfs-server-start-failed: the restart helper acts only on a failed
start with no nfsd running or stuck, never on a stop that hung (2026-11-18)."""

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "roles/nfs/files/nfs-server-start-failed"


def decide(result, threads=None, ps=""):
    with tempfile.NamedTemporaryFile("w") as f:
        if threads is not None:
            f.write(threads)
            f.flush()
        path = f.name if threads is not None else "/nonexistent/threads"
        return subprocess.run(["sh", str(CHECK), result, path, ps], timeout=30).returncode


class StartFailed(unittest.TestCase):
    def test_a_failed_start_with_nothing_running_is_restarted(self):
        self.assertEqual(decide("exit-code", "0\n", "S    nfsd"), 0)
        self.assertEqual(decide("exit-code"), 0, "no nfsd interface at all: nothing running")

    def test_a_hung_stop_is_left_alone(self):
        self.assertEqual(decide("timeout"), 1)
        self.assertEqual(decide("exit-code", "0\n", "D    nfsd\nS    bash"), 1)

    def test_running_threads_are_left_alone(self):
        self.assertEqual(decide("exit-code", "128\n"), 1)


if __name__ == "__main__":
    unittest.main()
