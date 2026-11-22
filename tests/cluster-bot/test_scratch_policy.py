"""cluster_bot.scratch_policy: one table decides a scratch request, every period counted
from the day the allocation was made: within the automatic size and period at once,
beyond them an administrator; giving space back is always at once; a nearly full disk
sends anything that grows to an administrator; a person has one allocation."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_bot/files"))
from cluster_bot import scratch_policy as sp  # noqa: E402

T, DAY = 1024**4, 86400
POLICY = {"auto_bytes": 5 * T, "auto_days": 90, "guard": 0.8}
ALICE = {"user": "alice", "bytes": 2 * T, "state": "active", "created": 0, "expires": 30 * DAY}


def listing(used=0.3, *allocations):
    return {"allocations": list(allocations), "disk": {"bytes": 100 * T, "used": used * 100 * T}}


def decide(request, used=0.3, now=0):
    return sp.decide(request, listing(used, ALICE), POLICY, now)[0]


class PolicyTests(unittest.TestCase):
    def test_a_new_allocation_within_the_table(self):
        self.assertEqual(decide({"op": "create", "user": "bob", "size": 5 * T, "days": 90}), "auto")
        self.assertEqual(decide({"op": "create", "user": "bob", "size": 6 * T, "days": 30}), "admin")
        self.assertEqual(decide({"op": "create", "user": "bob", "size": T, "days": 91}), "admin")

    def test_one_per_person(self):
        with self.assertRaises(sp.Refused):
            decide({"op": "create", "user": "alice", "size": T, "days": 7})
        with self.assertRaises(sp.Refused):
            decide({"op": "extend", "user": "bob", "days": 7})

    def test_resize_down_always_up_to_the_automatic_size(self):
        self.assertEqual(decide({"op": "resize", "user": "alice", "size": T}), "auto")
        self.assertEqual(decide({"op": "resize", "user": "alice", "size": 5 * T}), "auto")
        self.assertEqual(decide({"op": "resize", "user": "alice", "size": 6 * T}), "admin")

    def test_an_extension_counts_from_when_it_was_made(self):
        self.assertEqual(decide({"op": "extend", "user": "alice", "days": 60}), "auto")  # to day 90
        self.assertEqual(decide({"op": "extend", "user": "alice", "days": 61}), "admin")  # past day 90
        # Once its end passed, an extension counts from today.
        self.assertEqual(decide({"op": "extend", "user": "alice", "days": 50}, now=45 * DAY), "admin")

    def test_a_full_disk_sends_growth_to_an_administrator_never_giving_back(self):
        self.assertEqual(decide({"op": "create", "user": "bob", "size": T, "days": 7}, used=0.8), "admin")
        self.assertEqual(decide({"op": "extend", "user": "alice", "days": 7}, used=0.85), "admin")
        self.assertEqual(decide({"op": "resize", "user": "alice", "size": T}, used=0.95), "auto")
        self.assertEqual(decide({"op": "release", "user": "alice"}, used=0.95), "auto")


if __name__ == "__main__":
    unittest.main()
