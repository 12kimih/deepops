"""cluster_bot.scratch_policy: within the automatic size, period and extensions a scratch
request is applied at once; beyond them an administrator approves; giving space back is
always automatic; nobody acts on someone else's allocation."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_bot/files"))
from cluster_bot import scratch_policy as sp  # noqa: E402

T = 1024**4
POLICY = {"auto_bytes": 2 * T, "auto_days": 30, "auto_extensions": 2}
ALLOCS = [
    {"id": 1000, "user": "alice", "bytes": 1 * T, "state": "active", "extensions": 0},
    {"id": 1001, "user": "alice", "bytes": T // 2, "state": "frozen", "extensions": 2},
    {"id": 1002, "user": "bob", "bytes": T, "state": "released", "extensions": 0},
]


def decide(**request):
    return sp.decide(request, ALLOCS, POLICY)[0]


class PolicyTests(unittest.TestCase):
    def test_create_within_the_automatic_total_and_period(self):
        self.assertEqual(decide(op="create", user="bob", size=2 * T, days=30), "auto")
        self.assertEqual(decide(op="create", user="bob", size=2 * T, days=31), "admin")
        # alice already holds 1.5 T: 1 T more is over the automatic total.
        self.assertEqual(decide(op="create", user="alice", size=T, days=7), "admin")
        self.assertEqual(decide(op="create", user="alice", size=T // 4, days=7), "auto")

    def test_resize_down_always_up_within_the_total(self):
        self.assertEqual(decide(op="resize", user="alice", id=1000, size=T // 2), "auto")
        self.assertEqual(decide(op="resize", user="alice", id=1000, size=3 * T // 2), "auto")
        self.assertEqual(decide(op="resize", user="alice", id=1000, size=2 * T), "admin")

    def test_extend_within_the_automatic_count_and_period(self):
        self.assertEqual(decide(op="extend", user="alice", id=1000, days=14), "auto")
        self.assertEqual(decide(op="extend", user="alice", id=1000, days=60), "admin")
        self.assertEqual(decide(op="extend", user="alice", id=1001, days=7), "admin")

    def test_release_always_and_never_someone_elses(self):
        self.assertEqual(decide(op="release", user="alice", id=1001), "auto")
        for request in (
            {"op": "release", "user": "bob", "id": 1000},
            {"op": "extend", "user": "alice", "id": 9},
        ):
            with self.subTest(**request), self.assertRaises(sp.Refused):
                sp.decide(request, ALLOCS, POLICY)


if __name__ == "__main__":
    unittest.main()
