"""cluster_bot.data: a shared data request is checked (form, existing entries, the budget
with what is still downloading), always an administrator's to approve, applied through
the home server's service; each round tells whose download ended once, and an entry no
one but its requester said they use gets a notice, then goes after the grace period
unless someone presses its button."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_bot/files"))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files")
)  # cluster_tools, which the bot imports
from cluster_bot import data  # noqa: E402
from cluster_bot.requests import Failed, Refused  # noqa: E402

DAY = data.DAY
G = 1 << 30


class Service:
    """The home server's service as the bot sees it."""

    def __init__(self, entries=None, used=0, budget=100 * G):
        self.entries, self.used, self.budget, self.calls, self.broken = entries or {}, used, budget, [], set()

    def __call__(self, *words):
        self.calls.append(words)
        if words[0] in self.broken:
            raise Failed("no answer")
        if words[0] == "list":
            return {"entries": self.entries, "used": self.used, "budget": self.budget, "kinds": {}}
        if words[0] == "remove":
            self.entries.pop(f"{words[1]}/{words[2]}")
        return {}


def req(**more):
    return {"kind": "data", "user": "alice", "category": "datasets", "name": "coco",
            "url": "https://data.example/coco.tar", "size": 10 * G, "sha256": "", "purpose": "detection", **more}  # fmt: skip


class Feature(unittest.TestCase):
    def test_sizes_are_read_as_people_write_them(self):
        self.assertEqual([data.size_of(t) for t in ("120G", "1.5T", " 800 MiB", "12", "lots")],
                         [120 * G, int(1.5 * (1 << 40)), 800 << 20, 12, None])  # fmt: skip

    def test_a_request_is_checked_and_always_held_for_an_administrator(self):
        service = Service()
        feature = data.DataFeature(service, ["datasets", "models"], "/home/shared")
        self.assertEqual(feature.decide(req())[0], "admin")
        for bad in (req(category="secrets"), req(name="../x"), req(url="http://data.example/a"), req(size=None),
                    req(sha256="abc")):  # fmt: skip
            with self.subTest(bad=bad), self.assertRaises(Refused):
                feature.decide(bad)
        text, info = feature.apply(req())
        self.assertEqual(info["path"], "/home/shared/datasets/coco")
        self.assertEqual(service.calls[-1], ("fetch", "datasets", "coco", "https://data.example/coco.tar", "-",
                                             int(10 * G * data.SLACK), "alice"))  # fmt: skip

    def test_an_existing_entry_or_an_overrun_budget_is_refused(self):
        coming = {"datasets/big": {"state": "fetching", "max_bytes": 60 * G, "requester": "bob"}}
        feature = data.DataFeature(Service(coming, used=30 * G), ["datasets"], "/home/shared")
        with self.assertRaises(Refused) as e:
            feature.decide(req(size=10 * G))  # 30 used + 60 coming + 11 > 100
        self.assertIn("예산", str(e.exception))
        feature = data.DataFeature(Service({"datasets/coco": {"state": "done", "requester": "bob"}}),
                                   ["datasets"], "/home/shared")  # fmt: skip
        with self.assertRaises(Refused):
            feature.decide(req())
        failed = data.DataFeature(
            Service({"datasets/coco": {"state": "failed"}}), ["datasets"], "/home/shared"
        )
        self.assertEqual(failed.decide(req())[0], "admin")


class Watch(unittest.TestCase):
    def setUp(self):
        self.said = []
        self.service = Service({"datasets/coco": {"kind": "datasets", "name": "coco", "requester": "alice",
                                                  "state": "fetching", "asked": 0, "uses": {}}})  # fmt: skip
        self.feature = data.DataFeature(self.service, ["datasets"], "/home/shared")
        self.seen = {}

    def round(self, now):
        data.watch(
            self.feature,
            self.seen,
            now,
            28,
            14,
            lambda event, key, entry, **more: self.said.append((event, key)),
        )
        out, self.said = self.said, []
        return out

    def test_an_ended_download_is_told_once_and_the_first_round_only_learns(self):
        done = {
            "kind": "datasets",
            "name": "old",
            "requester": "bob",
            "state": "done",
            "ended": 5 * DAY,
            "uses": {},
        }
        self.service.entries["datasets/old"] = done
        self.assertEqual(self.round(6 * DAY), [])
        self.service.entries["datasets/coco"].update(state="done", ended=7 * DAY)
        self.assertEqual(self.round(7 * DAY), [("done", "datasets/coco")])
        self.assertEqual(self.round(8 * DAY), [])

    def test_unused_entries_get_a_notice_then_go_unless_someone_uses_them(self):
        self.round(0)
        self.service.entries["datasets/coco"].update(state="done", ended=DAY)
        self.round(DAY)
        self.assertEqual(self.round(28 * DAY), [], "not 28 days since it came")
        self.assertEqual(self.round(29 * DAY), [("notice", "datasets/coco")])
        self.assertEqual(self.round(30 * DAY), [], "one notice")
        # Someone presses the button: a recorded use ends the notice.
        self.service.entries["datasets/coco"]["uses"]["carol"] = 35 * DAY
        self.assertEqual(self.round(44 * DAY), [])
        self.assertEqual(self.seen["notices"], {})
        self.assertEqual(self.round(63 * DAY), [("notice", "datasets/coco")])
        self.service.broken.add("remove")
        self.assertEqual(self.round(77 * DAY), [("failed_removal", "datasets/coco")])
        self.service.broken.clear()
        self.assertEqual(self.round(77 * DAY), [("removed", "datasets/coco")])
        self.assertNotIn("datasets/coco", self.service.entries)
        self.assertEqual(self.round(78 * DAY), [])
        self.assertEqual((self.seen["told"], self.seen["notices"]), ({}, {}))

    def test_an_unreadable_service_changes_nothing(self):
        self.service.broken.add("list")
        with self.assertRaises(Failed):
            self.round(0)
        self.assertEqual(self.seen, {})


if __name__ == "__main__":
    unittest.main()
