"""cluster_tools.slack.keep: a message the bot keeps up to date stands once in its channel,
whatever happened to the record of it (lost, from before a restart, from before the
mark), and its thread holds every page of the full text, edited in place, the extra
pages deleted. pages() splits blocks within Slack's limits and cuts nothing."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
sys.path.insert(0, str(ROOT / "tests/lib"))
from cluster_tools import rich, slack  # noqa: E402
from fake_slack import FakeChannel  # noqa: E402


def card(n):
    return f"{n} items", rich.blocks(f"**{n} items**")


def thread(lines):
    return slack.pages("all items", rich.blocks("\n".join(lines)))


class Keep(unittest.TestCase):
    def setUp(self):
        self.slack, self.said = FakeChannel(), []

    def keep(self, n, lines=(), record=None, **kw):
        return slack.keep(self.slack, "#admin", "items", card(n), thread(lines) if lines else [], record,
                          log=self.said.append, **kw)  # fmt: skip

    def test_posted_once_then_edited_and_left_when_unchanged(self):
        record = self.keep(1, pin=True)
        record = self.keep(2, record=record)
        record = self.keep(2, record=record)
        self.assertEqual([m["text"] for m in self.slack.posts()], ["2 items"])
        self.assertEqual(self.slack.did("update"), [("update", "2 items")], "no edit that changes nothing")
        self.assertEqual(self.slack.pins, {record["ts"]})
        self.assertEqual(self.slack.at(record["ts"])["metadata"]["event_type"], slack.KEPT)

    def test_a_lost_record_finds_the_message_and_posts_no_second(self):
        """2026-11-26: two copies of the ban list in the admin channel."""
        first = self.keep(1, pin=True)
        self.slack.__dict__.pop("_kept_scanned")  # a restart
        again = self.keep(2, record=None, pin=True)
        self.assertEqual(len(self.slack.posts()), 1)
        self.assertEqual(again["ts"], first["ts"])
        self.assertEqual(self.slack.posts()[0]["text"], "2 items")

    def test_second_copies_are_deleted_with_their_replies(self):
        mark = slack.marked("items")
        old = self.slack.add("1 items", metadata=mark)
        self.slack.add("page", thread_ts=old, metadata=slack.marked("items", 1))
        newer = self.slack.add("1 items", metadata=mark)
        other = self.slack.add("1 items", metadata=mark, bot=("B9", "A9"))  # another app's: left
        person = self.slack.add("hello", bot=("", ""))
        record = self.keep(3)
        self.assertEqual(record["ts"], newer, "the newest is kept when none is recorded")
        self.assertEqual([m["ts"] for m in self.slack.posts()], [newer, other, person])
        self.assertEqual(self.slack.thread(old), [], "its page went with it")
        self.assertIn("deleted a second copy", " ".join(self.said))

    def test_the_recorded_copy_is_the_one_kept(self):
        mark = slack.marked("items")
        recorded = self.slack.add("1 items", metadata=mark)
        self.slack.add("1 items", metadata=mark)
        record = self.keep(1, record={"channel": "CADMIN", "ts": recorded})
        self.assertEqual(record["ts"], recorded)
        self.assertEqual([m["ts"] for m in self.slack.posts()], [recorded])

    def test_an_unmarked_copy_from_before_is_taken_over(self):
        old = self.slack.add("1 items")
        record = self.keep(1, legacy=lambda m: m["text"].endswith("items"), pin=True)
        self.assertEqual(record["ts"], old)
        self.assertEqual(self.slack.at(old)["metadata"], slack.marked("items"), "marked from now on")
        self.assertEqual(self.slack.pins, {old})

    def test_the_thread_holds_every_line_and_follows_the_text(self):
        many = [f"line {i:04d} " + "x" * 60 for i in range(600)]
        record = self.keep(600, many)
        replies = self.slack.thread(record["ts"])
        self.assertGreater(len(replies), 1, "a long list takes several replies")
        shown = "\n".join(rich.read(r["blocks"]) for r in replies)
        self.assertTrue(all(line in shown for line in many), "every line, nothing cut")
        for r in replies:
            self.assertLessEqual(len(r["blocks"]), slack.MAX_BLOCKS)
            self.assertLessEqual(len(rich.read(r["blocks"])), slack.MAX_PAGE)
        record = self.keep(2, many[:2], record=record)
        replies2 = self.slack.thread(record["ts"])
        self.assertEqual([r["ts"] for r in replies2], [replies[0]["ts"]], "edited in place, the rest deleted")
        self.assertEqual(rich.read(replies2[0]["blocks"]), "\n".join(many[:2]))
        self.keep(2, many[:2], record=record)
        self.assertEqual(len(self.slack.did("update")), 2, "an unchanged page is not edited")

    def test_a_message_deleted_by_hand_is_posted_again(self):
        record = self.keep(1)
        self.slack.messages.clear()
        record2 = self.keep(1, record=record)
        self.assertEqual(len(self.slack.posts()), 1)
        self.assertNotEqual(record2["ts"], record["ts"])

    def test_without_the_history_scopes_the_record_alone_keeps_it(self):
        self.slack.no_history = True
        record = self.keep(1, ["a", "b"])
        record = self.keep(2, ["a", "b", "c"], record=record)
        self.assertEqual(len(self.slack.posts()), 1)
        self.assertEqual(len(self.slack.thread(record["ts"])), 1)
        self.assertIn("Import the app manifest", self.said[0])


class Pages(unittest.TestCase):
    def test_nothing_is_cut_and_each_page_fits(self):
        blocks = rich.blocks("\n".join("y" * 100 for _ in range(1000)))
        out = slack.pages("title", blocks)
        self.assertEqual(sum(len(p[1]) for p in out), len(blocks))
        self.assertEqual(out[0][0], f"title (1/{len(out)})")
        self.assertEqual(slack.pages("title", blocks[:1]), [("title", blocks[:1])])


if __name__ == "__main__":
    unittest.main()
