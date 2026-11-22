"""cluster_bot.scratch through the request engine (cluster_bot.requests): a request within the automatic limits is applied at once, one
beyond them waits for an administrator (and survives a restart), a refusal of the
scratch server comes back as a message, and the command line sent over SSH is exactly
the request."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_bot/files"))
from cluster_bot import requests, scratch  # noqa: E402

T = 1024**4
POLICY = {"auto_bytes": 5 * T, "auto_days": 90, "guard": 0.8}


class Server:
    """A stand-in for scratch-alloc on the scratch server: one allocation per person."""

    def __init__(self):
        self.allocs, self.calls = [], []

    def __call__(self, *words):
        self.calls.append(words)
        if words[0] == "list":
            mine = [a for a in self.allocs if len(words) == 1 or a["user"] == words[1]]
            return {"allocations": mine, "disk": {"bytes": 100 * T, "used": 10 * T}}
        if words[0] == "create":
            if words[1] == "nobody":
                raise requests.Failed("no account 'nobody'")
            size = int(words[2].rstrip("K")) * 1024
            a = {"user": words[1], "bytes": size, "state": "active", "path": f"/scratch/{words[1]}",
                 "created": 0, "expires": 0}  # fmt: skip
            self.allocs.append(a)
            return a
        a = next(a for a in self.allocs if a["user"] == words[1])
        if words[0] == "release":
            a["state"] = "released"
        return a


class ScratchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = Server()
        self.pending = os.path.join(self.tmp.name, "pending.json")
        self.service = requests.Engine({"scratch": scratch.ScratchFeature(self.server, POLICY)}, self.pending)

    def test_within_the_limits_it_is_applied_at_once(self):
        out = self.service.submit(
            {"kind": "scratch", "op": "create", "user": "alice", "size": T, "days": 7, "reason": "run"}
        )
        self.assertEqual(out.status, "done")
        self.assertIn("/scratch/alice", out.text)
        self.assertEqual(self.server.calls[-1], ("create", "alice", f"{T // 1024}K", 7, "run"))

    def test_beyond_them_an_administrator_approves_even_after_a_restart(self):
        out = self.service.submit(
            {"kind": "scratch", "op": "create", "user": "alice", "size": 6 * T, "days": 7}
        )
        self.assertEqual(out.status, "held")
        self.assertEqual(len(self.server.allocs), 0)
        again = requests.Engine(
            {"scratch": scratch.ScratchFeature(self.server, POLICY)}, self.pending
        )  # restarted
        done = again.approve(out.key, "admin1")
        self.assertEqual(done.status, "done")
        self.assertIn("승인: admin1", done.text)
        self.assertEqual(again.approve(out.key, "admin1").status, "refused")  # only once

    def test_denied_and_refused_requests_say_why(self):
        held = self.service.submit(
            {"kind": "scratch", "op": "create", "user": "alice", "size": 6 * T, "days": 7}
        )
        self.assertIn("거절", self.service.deny(held.key, "admin1", "too big").text)
        out = self.service.submit({"kind": "scratch", "op": "create", "user": "nobody", "size": T, "days": 7})
        self.assertEqual(out.status, "refused")
        self.assertIn("no account", out.text)
        out = self.service.submit({"kind": "scratch", "op": "release", "user": "bob"})
        self.assertEqual(out.status, "refused", "bob has no allocation")

    def test_release_is_always_immediate(self):
        self.service.submit({"kind": "scratch", "op": "create", "user": "alice", "size": T, "days": 7})
        out = self.service.submit({"kind": "scratch", "op": "release", "user": "alice"})
        self.assertEqual(out.status, "done")
        self.assertEqual(self.server.allocs[0]["state"], "released")


if __name__ == "__main__":
    unittest.main()
