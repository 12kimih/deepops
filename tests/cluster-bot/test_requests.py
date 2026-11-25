"""cluster_bot.requests: one flow for every feature. A feature gives only its decision,
its action and a description; the engine applies at once or holds (surviving a restart),
approves and denies once, refuses an unknown kind, and turns a feature's failure into a
message."""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_bot/files"))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files")
)  # cluster_tools, which the bot imports
from cluster_bot import requests, state  # noqa: E402


class Echo(requests.Feature):
    """A feature that is not scratch: auto under 10, admin above, fails on 7."""

    def __init__(self):
        self.applied = []

    def decide(self, req):
        if req["n"] < 0:
            raise requests.Refused("negative")
        return ("auto", "small") if req["n"] < 10 else ("admin", "large")

    def apply(self, req):
        if req["n"] == 7:
            raise requests.Failed("unlucky")
        self.applied.append(req["n"])
        return f"did {req['n']}", {"n": req["n"]}

    def describe(self, req):
        return f"echo {req['n']}"


class EngineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "bot", "pending.json")
        self.echo = Echo()
        self.engine = requests.Engine({"echo": self.echo}, self.path)

    def test_auto_applies_and_admin_holds_across_a_restart(self):
        self.assertEqual(self.engine.submit({"kind": "echo", "user": "a", "n": 3}).status, "done")
        held = self.engine.submit({"kind": "echo", "user": "a", "n": 30})
        self.assertEqual((held.status, self.echo.applied), ("held", [3]))
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        again = requests.Engine({"echo": self.echo}, self.path)
        self.assertEqual(again.approve(held.key, "boss").status, "done")
        self.assertEqual(self.echo.applied, [3, 30])
        self.assertEqual(again.approve(held.key, "boss").text, "이미 처리된 요청입니다.")

    def test_two_approvals_at_once_apply_it_once(self):
        # Slack handlers run on threads: both approvals read the held request before
        # either writes, unless the whole read, take, write is one locked step.
        held = self.engine.submit({"kind": "echo", "user": "a", "n": 30})
        meet, real = threading.Barrier(2), state.read

        def read(path, default):
            try:
                meet.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass
            return real(path, default)

        outs = []
        with mock.patch.object(state, "read", read):
            runs = [threading.Thread(target=lambda: outs.append(self.engine.approve(held.key, "boss")))
                    for _ in range(2)]  # fmt: skip
            for run in runs:
                run.start()
            for run in runs:
                run.join()
        self.assertEqual(self.echo.applied, [30])
        self.assertEqual(sorted(o.status for o in outs), ["done", "refused"])

    def test_a_change_that_raises_writes_nothing(self):
        state.write(self.path, {"a": 1})
        with self.assertRaises(KeyError), state.locked(self.path, {}) as doc:
            doc["a"] = 2
            raise KeyError
        self.assertEqual(state.read(self.path, None), {"a": 1})

    def test_deny_refusal_failure_and_unknown_kind_are_messages(self):
        held = self.engine.submit({"kind": "echo", "user": "a", "n": 30})
        self.assertIn("echo 30", self.engine.deny(held.key, "boss", "no").text)
        self.assertEqual(self.engine.submit({"kind": "echo", "user": "a", "n": -1}).status, "refused")
        self.assertIn("unlucky", self.engine.submit({"kind": "echo", "user": "a", "n": 7}).text)
        self.assertEqual(self.engine.submit({"kind": "nope", "user": "a"}).status, "refused")


if __name__ == "__main__":
    unittest.main()
