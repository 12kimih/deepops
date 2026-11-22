"""cluster_tools.notices: who is told this run. Never told, told REPEAT ago (a little
early with LEEWAY), or urgent; someone no longer current is forgotten."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from cluster_tools import notices  # noqa: E402


class NoticeTests(unittest.TestCase):
    def test_first_then_every_repeat_urgent_always_and_forgotten_when_gone(self):
        told = {}
        self.assertEqual(notices.due(["a", "b"], told, 0, 100), ["a", "b"])
        told.update(a=0, b=0)
        self.assertEqual(notices.due(["a", "b"], told, 50, 100), [])
        self.assertEqual(notices.due(["a", "b"], told, 50, 100, urgent={"b"}), ["b"])
        self.assertEqual(notices.due(["a", "b"], told, 95, 100, leeway=10), ["a", "b"], "a little early")
        self.assertEqual(notices.due(["b"], told, 96, 100), [])
        self.assertEqual(told, {"b": 0}, "a is no longer current: forgotten, told at once if back")
        self.assertEqual(notices.due(["a", "b"], told, 97, 100), ["a"])

    def test_a_damaged_entry_is_never_told(self):
        told = {"a": "yesterday", "b": True, "c": 5}
        self.assertEqual(notices.due(["a", "b", "c"], told, 6, 100), ["a", "b"])
        self.assertIsNone(notices.told_at(told, "a"))


if __name__ == "__main__":
    unittest.main()
