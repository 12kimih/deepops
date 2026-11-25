"""cluster_bot.notices: the user docs' notices go to the announcement channel through the
cluster's Slack app, each once, on its date; a first deploy repeats none."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_bot/files"))
from cluster_bot import notices  # noqa: E402


def notice(nid, day):
    return {
        "id": f"https://docs.example.org/news/{nid}/",
        "date": day,
        "text": nid,
        "blocks": [{"type": "x"}],
    }


class Round(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.seen = os.path.join(self.dir.name, "notices-posted.json")
        self.doc = {"notices": [notice("old", "2026-01-01"), notice("today", "2026-02-01"),
                                notice("later", "2026-03-01")]}  # fmt: skip
        self.posted = []

    def tearDown(self):
        self.dir.cleanup()

    def run_round(self, today, post=None):
        notices.round_(
            lambda: self.doc, post or (lambda text, blocks: self.posted.append(text)), self.seen, today
        )

    def test_the_first_round_posts_nothing_and_counts_what_is_due_as_announced(self):
        self.run_round("2026-02-01")
        self.assertEqual(self.posted, [])
        self.assertEqual(
            sorted(e["date"] for e in json.loads(Path(self.seen).read_text()).values()),
            ["2026-01-01", "2026-02-01"],
        )

    def test_a_notice_goes_out_once_on_its_date(self):
        self.run_round("2026-02-01")
        self.run_round("2026-02-15")
        self.assertEqual(self.posted, [])
        self.run_round("2026-03-01")
        self.run_round("2026-03-01")
        self.assertEqual(self.posted, ["later"])

    def test_a_new_notice_already_due_is_posted_oldest_first(self):
        self.run_round("2026-01-01")
        self.doc["notices"].append(notice("new", "2026-01-15"))
        self.run_round("2026-03-01")
        self.assertEqual(self.posted, ["new", "today", "later"])

    def test_a_failed_post_keeps_what_went_out_and_retries_the_rest(self):
        self.run_round("2026-01-01")

        def flaky(text, blocks):
            if text == "later":
                raise RuntimeError("slack down")
            self.posted.append(text)

        with self.assertRaises(RuntimeError):
            self.run_round("2026-03-01", flaky)
        self.run_round("2026-03-01")
        self.assertEqual(self.posted, ["today", "later"])

    def test_each_posted_notice_is_pinned_so_the_newest_stays(self):
        self.run_round("2026-01-01")
        pinned = []

        def post(text, blocks):
            self.posted.append(text)
            return f"ts-{text}"

        notices.round_(lambda: self.doc, post, self.seen, "2026-03-01", pin=pinned.append)
        self.assertEqual(pinned, ["ts-today", "ts-later"])

    def test_a_malformed_notice_is_left_out(self):
        self.run_round("2026-01-01")
        self.doc["notices"] += [{"id": "x", "date": "2026-01-02"}, "junk"]
        self.run_round("2026-01-02")
        self.assertEqual(self.posted, [])

    def test_an_unreachable_site_seeds_nothing(self):
        def down():
            raise OSError("no route")

        with self.assertRaises(OSError):
            notices.round_(down, self.posted.append, self.seen, "2026-02-01")
        self.assertFalse(os.path.exists(self.seen))


class Edits(unittest.TestCase):
    """A notice reworded on the site after the bot posted it: the post is edited in place,
    once; before, the Slack post kept the old text and only a one-off script fixed it."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.seen = os.path.join(self.dir.name, "notices-posted.json")
        self.pinned = os.path.join(self.dir.name, "notices-pinned.json")
        self.doc = {"notices": [notice("old", "2026-01-01")]}
        self.posted, self.updated = [], []

    def tearDown(self):
        self.dir.cleanup()

    def post(self, text, blocks):
        self.posted.append(text)
        return f"ts-{len(self.posted)}"

    def run_round(self, today="2026-03-01"):
        notices.round_(lambda: self.doc, self.post, self.seen, today,
                       update=lambda ts, text, blocks: self.updated.append((ts, text, blocks)),
                       pinned_path=self.pinned)  # fmt: skip

    def test_a_reworded_notice_edits_its_post_once(self):
        self.run_round("2026-01-01")
        self.doc["notices"].append(notice("new", "2026-02-01"))
        self.run_round()
        self.run_round()
        self.assertEqual((self.posted, self.updated), (["new"], []), "unchanged: nothing edited")
        self.doc["notices"][1]["text"] = "new, reworded"
        self.doc["notices"][1]["blocks"] = [{"type": "y"}]
        self.run_round()
        self.run_round()
        self.assertEqual(self.updated, [("ts-1", "new, reworded", [{"type": "y"}])])
        self.assertEqual(self.posted, ["new"], "edited, never posted again")

    def test_a_notice_seen_on_the_first_round_has_no_post_to_edit(self):
        self.run_round("2026-01-01")
        self.doc["notices"][0]["text"] = "old, reworded"
        self.run_round()
        self.assertEqual((self.posted, self.updated), ([], []))

    def test_the_newest_notice_from_before_the_ts_was_kept_is_the_pinned_one(self):
        Path(self.seen).write_text(json.dumps({notice("old", "2026-01-01")["id"]: "2026-01-01",
                                               notice("new", "2026-02-01")["id"]: "2026-02-01"}))  # fmt: skip
        Path(self.pinned).write_text(json.dumps({"ts": "ts-pinned"}))
        self.doc["notices"].append(notice("new", "2026-02-01"))
        self.run_round()
        self.run_round()
        self.assertEqual(self.updated, [("ts-pinned", "new", [{"type": "x"}])], "brought up to date once")
        self.assertEqual(self.posted, [])
        entries = json.loads(Path(self.seen).read_text())
        self.assertEqual(entries[notice("old", "2026-01-01")["id"]], {"date": "2026-01-01"})


if __name__ == "__main__":
    unittest.main()
