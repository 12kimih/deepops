"""cluster_bot.notices: the user docs' notices go to the announcement channel through the
cluster's Slack app, each once, on its date; a first deploy repeats none."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_bot/files"))
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))  # cluster_tools, which the bot imports
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


def md_notice(nid, day, lead="**팀 QoS**로 제출해 주세요."):
    return {"id": f"https://docs.example.org/news/{nid}/", "date": day, "text": nid, "title": f":mega: {nid}",
            "markdown": lead, "link": {"text": "자세히 보기", "url": f"https://docs.example.org/news/{nid}/"},
            "context": "정책  |  " + day}  # fmt: skip


class Markdown(unittest.TestCase):
    """The docs send each notice as Markdown and the bot renders it with cluster_tools.rich,
    the one renderer: the docs keep no converter of their own (two of one grammar drift)."""

    def test_a_notice_is_rendered_by_rich(self):
        msg = notices.render(md_notice("qos", "2026-02-01"))
        self.assertEqual([b["type"] for b in msg["blocks"]], ["header", "rich_text", "actions", "context"])
        self.assertEqual(msg["blocks"][1]["elements"][0]["elements"][0],
                         {"type": "text", "text": "팀 QoS", "style": {"bold": True}}, "bold before a particle")  # fmt: skip
        self.assertEqual(msg["blocks"][2]["elements"][0]["url"], "https://docs.example.org/news/qos/")

    def test_a_notice_of_blocks_is_posted_as_it_is(self):
        old = notice("old", "2026-01-01")
        self.assertEqual(notices.render(old), {"text": "old", "blocks": [{"type": "x"}]})

    def test_check_finds_what_the_bot_could_not_show(self):
        good = {"notices": [md_notice("qos", "2026-02-01")]}
        self.assertEqual(notices.check(good), [])
        bad = {"notices": [md_notice("a", "2026-02-01", "*하나의 별*과 ## 제목"), md_notice("b" * 160, "2026-02-01"),
                           {"id": "c", "date": "2026-02-01"}]}  # fmt: skip
        found = notices.check(bad)
        self.assertEqual(len(found), 3, found)
        self.assertIn("title", found[1])
        self.assertEqual(notices.check({"nope": []}), ['notices.json: no "notices" list'])

    def test_a_rendering_change_edits_no_post_a_reworded_notice_does(self):
        with tempfile.TemporaryDirectory() as d:
            seen, updated = os.path.join(d, "seen.json"), []
            doc = {"notices": [md_notice("qos", "2026-01-01")]}

            def run():
                notices.round_(lambda: doc, lambda text, blocks: "ts-1", seen, "2026-02-01",
                               update=lambda ts, text, blocks: updated.append(text))  # fmt: skip

            Path(seen).write_text("{}")
            run()
            with mock.patch.object(notices.rich, "blocks", lambda text: []):
                run()
            self.assertEqual(updated, [], "the digest is of the Markdown, not of its rendering")
            doc["notices"][0]["markdown"] = "**팀 QoS**로 제출해 주세요. 바뀐 문장."
            run()
            run()
            self.assertEqual(updated, ["qos"])


if __name__ == "__main__":
    unittest.main()
