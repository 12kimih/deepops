"""cluster_bot.notices: the announcement channel kept like the user docs' notices.json,
through the cluster's Slack app: each notice posted once on its date, edited when it is
reworded or rendered anew, deleted when the docs drop it, the newest pinned; a first
deploy repeats none. Before, a dropped notice's post stayed and a post kept the markup
of the code that first rendered it."""

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
from cluster_tools.slack import Error  # noqa: E402

sys.path.insert(0, str(ROOT / "tests/lib"))
from fake_slack import pins_call, refuse_foreign  # noqa: E402

CHANNEL = "C1"
SITE = "https://docs.example.org/news/"


def notice(nid, day, pin_until="2099-12-31"):
    return {"id": f"{SITE}{nid}/", "date": day, "text": nid, "blocks": [
        {"type": "header", "text": {"type": "plain_text", "text": f":mega: {nid}"}},
        {"type": "actions", "elements": [{"type": "button", "url": f"{SITE}{nid}/"}]}],
        **({"pin_until": pin_until} if pin_until else {})}  # fmt: skip


class FakeSlack:
    """The Slack Web API the round calls, over one channel's messages."""

    def __init__(self):
        self.messages, self.replies, self.pins, self.log = [], {}, set(), []
        self.n, self.fail, self.locked = 0, set(), set()

    def add(self, ts, text, blocks=None, bot="B1", app="A1", metadata=None, replies=()):
        self.messages.append({"ts": ts, "bot_id": bot, "app_id": app, "text": text, "blocks": blocks or [],
                              **({"metadata": metadata} if metadata else {}),
                              **({"reply_count": len(replies)} if replies else {})})  # fmt: skip
        self.replies[ts] = list(replies)

    def call(self, method, **p):
        if method == "auth.test":
            return {"ok": True, "bot_id": "B1"}
        if method == "bots.info":
            return {"ok": True, "bot": {"app_id": "A1"}}
        if method == "conversations.history":
            shown = [
                {**m, **({"pinned_to": [p["channel"]]} if m["ts"] in self.pins else {})}
                for m in self.messages
            ]
            return {"ok": True, "messages": sorted(shown, key=lambda m: -float(m["ts"]))}
        if method == "conversations.replies":
            return {"ok": True, "messages": [{"ts": p["ts"]}, *self.replies.get(p["ts"], [])]}
        self.log.append((method, p.get("ts") or p.get("timestamp")))
        if method == "chat.delete":
            if p["ts"] in self.locked:
                raise Error("Slack chat.delete failed: cant_delete_message")
            reply = next((r for rs in self.replies.values() for r in rs if r["ts"] == p["ts"]), None)
            refuse_foreign(method, reply or next(m for m in self.messages if m["ts"] == p["ts"]), "B1")
            self.messages = [m for m in self.messages if m["ts"] != p["ts"]]
            for ts, rs in self.replies.items():
                self.replies[ts] = [r for r in rs if r["ts"] != p["ts"]]
        elif method in ("pins.add", "pins.remove"):
            return pins_call(self.pins, method, p)
        return {"ok": True}

    def post(self, channel, text, blocks=None, metadata=None, cls=None):
        if text in self.fail:
            raise RuntimeError("slack down")
        self.n += 1
        ts = f"{1000 + self.n}.0"
        self.add(ts, text, blocks, metadata=metadata)
        self.log.append(("post", text))
        return ts

    def update(self, channel, ts, text, blocks=None, metadata=None):
        m = next(m for m in self.messages if m["ts"] == ts)
        refuse_foreign("chat.update", m, "B1")
        m.update(text=text, blocks=blocks, **({"metadata": metadata} if metadata else {}))
        self.log.append(("update", text))

    def posts(self):
        return sorted(m["text"] for m in self.messages)

    def did(self, *kinds):
        return [e for e in self.log if e[0] in kinds]


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.seen = os.path.join(self.dir.name, "notices-posted.json")
        self.pinned = os.path.join(self.dir.name, "notices-pinned.json")
        self.doc = {"pins_max": 1, "notices": [notice("old", "2026-01-01"), notice("today", "2026-02-01"),
                                               notice("later", "2026-03-01")]}  # fmt: skip
        self.slack, self.said = FakeSlack(), []

    def tearDown(self):
        self.dir.cleanup()

    def run_round(self, today="2026-03-01", **kw):
        return notices.round_(lambda: self.doc, self.slack, CHANNEL, self.seen, self.pinned, today,
                              self.said.append, **kw)  # fmt: skip

    def record(self):
        return json.loads(Path(self.seen).read_text())


class Round(Base):
    def test_the_first_round_posts_nothing_and_counts_what_is_due_as_announced(self):
        self.run_round("2026-02-01")
        self.assertEqual(self.slack.did("post"), [])
        self.assertEqual(sorted(e["date"] for e in self.record().values()), ["2026-01-01", "2026-02-01"])

    def test_a_notice_goes_out_once_on_its_date(self):
        self.run_round("2026-02-01")
        self.run_round("2026-02-15")
        self.assertEqual(self.slack.did("post"), [])
        self.run_round("2026-03-01")
        self.run_round("2026-03-01")
        self.assertEqual(self.slack.did("post"), [("post", "later")])

    def test_a_new_notice_already_due_is_posted_oldest_first(self):
        self.run_round("2026-01-01")
        self.doc["notices"].append(notice("new", "2026-01-15"))
        self.run_round()
        self.assertEqual(self.slack.did("post"), [("post", "new"), ("post", "today"), ("post", "later")])

    def test_a_failed_post_keeps_what_went_out_and_retries_the_rest(self):
        self.run_round("2026-01-01")
        self.slack.fail.add("later")
        with self.assertRaises(RuntimeError):
            self.run_round()
        self.slack.fail.clear()
        self.run_round()
        self.assertEqual(self.slack.did("post"), [("post", "today"), ("post", "later")])

    def test_the_newest_post_is_the_one_pinned(self):
        self.run_round("2026-01-01")
        self.run_round("2026-02-01")
        self.run_round("2026-03-01")
        newest = self.record()[notice("later", "2026-03-01")["id"]]["ts"]
        self.assertEqual(self.slack.pins, {newest}, "the earlier pin was taken off")

    def test_a_pin_whose_record_was_lost_is_taken_off(self):
        """A lost pinned record left the earlier notice pinned beside the newest."""
        self.run_round("2026-01-01")
        self.run_round("2026-02-01")
        first = self.record()[notice("today", "2026-02-01")["id"]]["ts"]
        self.assertEqual(self.slack.pins, {first})
        os.unlink(self.pinned)
        self.run_round("2026-03-01")
        newest = self.record()[notice("later", "2026-03-01")["id"]]["ts"]
        self.assertEqual(self.slack.pins, {newest}, f"{first} unpinned, one pinned notice")

    def test_a_pin_slack_has_whose_record_was_lost_is_recorded(self):
        """The owner's run stopped on pins.add already_pinned: the newest post was pinned
        in Slack while the pinned record did not name it."""
        self.run_round("2026-01-01")
        self.run_round("2026-02-01")
        first = self.record()[notice("today", "2026-02-01")["id"]]["ts"]
        os.unlink(self.pinned)
        self.run_round("2026-02-01")
        self.assertEqual(self.slack.pins, {first})
        self.assertEqual(json.loads(Path(self.pinned).read_text())["pins"], [first])

    def test_the_newest_unpinned_by_hand_is_pinned_again(self):
        self.run_round("2026-01-01")
        self.run_round("2026-02-01")
        pinned = set(self.slack.pins)
        self.slack.pins.clear()
        self.run_round("2026-02-01")
        self.assertEqual(self.slack.pins, pinned)

    def test_a_malformed_notice_is_left_out(self):
        self.run_round("2026-01-01")
        self.doc["notices"] += [{"id": "x", "date": "2026-01-02"}, "junk"]
        self.run_round("2026-01-02")
        self.assertEqual(self.slack.did("post"), [])

    def test_an_unreachable_site_seeds_nothing(self):
        def down():
            raise OSError("no route")

        with self.assertRaises(OSError):
            notices.round_(down, self.slack, CHANNEL, self.seen, self.pinned, "2026-02-01", self.said.append)
        self.assertFalse(os.path.exists(self.seen))

    def test_an_empty_export_deletes_nothing(self):
        self.run_round("2026-01-01")
        self.run_round()
        self.doc["notices"] = []
        self.run_round()
        self.assertEqual(self.slack.did("chat.delete"), [])
        self.assertEqual(self.slack.posts(), ["later", "today"])


class Reconcile(Base):
    """The channel follows notices.json: what was posted is edited, dropped or kept."""

    def test_every_post_carries_its_notice_in_metadata(self):
        self.run_round("2026-01-01")
        self.run_round()
        self.assertEqual([m["metadata"] for m in self.slack.messages],
                         [notices.metadata(notice("today", "")["id"]),
                          notices.metadata(notice("later", "")["id"])])  # fmt: skip

    def test_a_reworded_notice_edits_its_post_once(self):
        self.run_round("2026-01-01")
        self.run_round()
        self.run_round()
        self.assertEqual(self.slack.did("update"), [], "unchanged: nothing edited")
        self.doc["notices"][2]["text"] = "later, reworded"
        self.run_round()
        self.run_round()
        self.assertEqual(self.slack.did("update"), [("update", "later, reworded")])
        self.assertEqual(self.slack.posts(), ["later, reworded", "today"], "edited, never posted again")

    def test_a_new_renderer_edits_every_post_once(self):
        self.doc = {"notices": [md_notice("qos", "2026-02-01")]}
        self.run_round("2026-01-01")
        self.run_round()
        render = notices.render
        newer = lambda n: {**render(n), "blocks": [*render(n)["blocks"], {"type": "divider"}]}  # noqa: E731
        with mock.patch.object(notices, "render", newer):
            self.run_round()
            self.run_round()
        self.assertEqual(self.slack.did("update"), [("update", "qos")])
        self.assertEqual(
            self.slack.messages[0]["blocks"][-1], {"type": "divider"}, "as today's code renders it"
        )

    def test_a_dropped_notice_loses_its_post_and_the_bots_replies(self):
        self.run_round("2026-01-01")
        self.run_round()
        ts = self.record()[notice("today", "")["id"]]["ts"]
        m = next(m for m in self.slack.messages if m["ts"] == ts)
        m["reply_count"] = 2
        self.slack.replies[ts] = [{"ts": "1.5", "bot_id": "B1"}, {"ts": "1.6", "user": "U7"}]
        del self.doc["notices"][1]
        self.run_round()
        self.assertEqual(self.slack.did("chat.delete"), [("chat.delete", "1.5"), ("chat.delete", ts)])
        self.assertEqual(self.slack.posts(), ["later"])
        self.assertNotIn(notice("today", "")["id"], self.record())
        self.assertIn("1 replies of others left", self.said[-1])

    def test_a_post_is_found_by_its_metadata_when_the_record_is_lost(self):
        self.run_round("2026-01-01")
        self.run_round()
        Path(self.seen).write_text(json.dumps({n["id"]: {"date": n["date"]} for n in self.doc["notices"]}))
        self.doc["notices"][1]["text"] = "today, reworded"
        self.run_round()
        self.assertEqual(self.slack.did("post"), [("post", "today"), ("post", "later")], "none again")
        self.assertIn(("update", "today, reworded"), self.slack.did("update"))

    def test_a_post_deleted_by_hand_is_not_posted_again(self):
        self.run_round("2026-01-01")
        self.run_round()
        self.slack.messages = [m for m in self.slack.messages if m["text"] != "today"]
        self.run_round()
        self.assertEqual(self.slack.did("post"), [("post", "today"), ("post", "later")])

    def test_another_apps_post_is_never_touched(self):
        self.run_round("2026-01-01")
        self.slack.add("5.0", "gone", notice("gone", "")["blocks"], bot="B9", app="A9")
        self.run_round(adopt=True)
        self.assertNotIn(("chat.delete", "5.0"), self.slack.log)
        self.assertTrue(any(a["do"] == "foreign" for a in self.run_round(dry_run=True)))

    def test_without_the_history_scope_new_notices_still_go_out(self):
        self.run_round("2026-01-01")
        history = self.slack.call

        def call(method, **p):
            if method == "conversations.history":
                raise Error("Slack conversations.history failed: missing_scope")
            return history(method, **p)

        self.slack.call = call
        self.run_round()
        self.assertEqual(self.slack.did("post"), [("post", "today"), ("post", "later")])
        self.assertIn("import the app manifest", self.said[0])

    def test_a_post_the_bot_may_not_delete_is_said_and_the_round_goes_on(self):
        self.run_round("2026-01-01")
        self.slack.add("5.0", "gone", notice("gone", "")["blocks"], metadata=notices.metadata(f"{SITE}gone/"))
        self.slack.locked.add("5.0")
        self.run_round()
        self.assertIn("not allowed", " ".join(self.said))
        self.assertEqual(self.slack.did("post"), [("post", "today"), ("post", "later")])


class OldPosts(Base):
    """Posts from before metadata (the 11-19 notices the docs since dropped, a webhook's):
    found by the button to their page or by their title; the bot's rounds leave them, the
    owner's run (adopt) changes them after the dry run said what it would do."""

    def setUp(self):
        super().setUp()
        Path(self.seen).write_text(json.dumps({n["id"]: n["date"] for n in self.doc["notices"][:2]}))
        self.slack.add("1.0", "old", notice("old", "")["blocks"])  # its button: found by link
        self.slack.add("2.0", "today", [notice("today", "")["blocks"][0]])  # a title only
        self.slack.add("3.0", "dropped", notice("dropped", "")["blocks"])  # the docs dropped it
        self.slack.add("4.0", "chat", [])  # no notice: never touched

    def test_the_bots_round_leaves_them_and_posts_none_again(self):
        self.run_round("2026-02-01")
        self.assertEqual(self.slack.did("post", "update", "chat.delete"), [])

    def test_the_dry_run_says_what_the_owners_run_does_and_does_nothing(self):
        before = Path(self.seen).read_text()
        actions = self.run_round("2026-02-01", dry_run=True, adopt=True)
        self.assertEqual([(a["do"], a.get("ts"), a.get("how")) for a in actions],
                         [("delete", "3.0", "link"), ("update", "1.0", "link"), ("update", "2.0", "title"),
                          ("pin", None, None)])  # fmt: skip
        self.assertEqual(self.slack.log, [], "no call that changes Slack")
        self.assertEqual(Path(self.seen).read_text(), before, "nor the record")
        self.assertTrue(any(line.startswith("delete 3.0") for line in self.said), self.said)
        self.assertTrue(all("''" not in line for line in self.said), "each line names its notice")

    def test_the_owners_run_brings_them_to_the_notices(self):
        self.run_round("2026-02-01", adopt=True)
        self.assertEqual(self.slack.posts(), ["chat", "old", "today"])
        ids = {m["metadata"]["event_payload"]["id"] for m in self.slack.messages if "metadata" in m}
        self.assertEqual(
            ids, {notice("old", "")["id"], notice("today", "")["id"]}, "found exactly from now on"
        )
        self.assertEqual(self.slack.pins, {"2.0"})
        self.slack.log.clear()
        self.run_round("2026-02-01")
        self.assertEqual(self.slack.did("post", "update", "chat.delete"), [], "converged")

    def test_a_second_post_of_one_notice_goes(self):
        self.slack.add("6.0", "old", notice("old", "")["blocks"])
        self.run_round("2026-02-01", adopt=True)
        self.assertIn(("chat.delete", "6.0"), self.slack.log, "the first post stays")

    def test_the_newest_entry_from_before_the_ts_was_kept_is_the_pinned_one(self):
        Path(self.pinned).write_text(json.dumps({"ts": "2.0"}))
        self.run_round("2026-02-01")
        self.assertEqual(
            self.slack.did("update"), [("update", "today")], "a recorded post: brought up to date"
        )
        self.assertEqual(self.record()[notice("old", "")["id"]], {"date": "2026-01-01"})


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
        self.assertEqual(notices.render(old), {"text": "old", "blocks": old["blocks"]})

    def test_check_finds_what_the_bot_could_not_show(self):
        good = {"notices": [md_notice("qos", "2026-02-01")]}
        self.assertEqual(notices.check(good), [])
        bad = {"notices": [md_notice("a", "2026-02-01", "*하나의 별*과 ## 제목"), md_notice("b" * 160, "2026-02-01"),
                           {"id": "c", "date": "2026-02-01"}]}  # fmt: skip
        found = notices.check(bad)
        self.assertEqual(len(found), 3, found)
        self.assertIn("title", found[1])
        self.assertEqual(notices.check({"nope": []}), ['notices.json: no "notices" list'])


class Pins(Base):
    """The pins are what people must know now, as the docs say (pin_until, pins_max):
    before, the newest notice alone was pinned, so a rule change came off the pins with the
    next notice of any kind."""

    def setUp(self):
        super().setUp()
        self.doc = {"pins_max": 2, "notices": [
            notice("rule", "2026-01-01", "2026-03-01"), notice("plain", "2026-01-10", None),
            notice("downtime", "2026-01-20", "2026-01-25")]}  # fmt: skip
        self.run_round("2025-12-01")  # the first round: nothing due yet, nothing posted

    def pinned_ids(self):
        ids = {e["ts"]: nid.rstrip("/").rsplit("/", 1)[-1] for nid, e in self.record().items() if e.get("ts")}
        return sorted(ids[ts] for ts in self.slack.pins)

    def test_each_notice_stands_pinned_until_its_day(self):
        self.run_round("2026-01-01")
        self.assertEqual(self.pinned_ids(), ["rule"])
        self.run_round("2026-01-10")
        self.assertEqual(
            self.pinned_ids(), ["rule"], "a plain notice is not pinned, nor does it unpin the rule"
        )
        self.run_round("2026-01-20")
        self.assertEqual(self.pinned_ids(), ["downtime", "rule"])
        self.run_round("2026-01-26")
        self.assertEqual(self.pinned_ids(), ["rule"], "the downtime is over")
        self.run_round("2026-03-02")
        self.assertEqual(self.pinned_ids(), [], "the rule's days are over")
        self.assertEqual(json.loads(Path(self.pinned).read_text()), {"pins": []})

    def test_at_most_pins_max_the_newest(self):
        self.doc["pins_max"] = 1
        self.run_round("2026-01-20")
        self.assertEqual(self.pinned_ids(), ["downtime"])

    def test_an_export_that_sets_no_pins_max_pins_nothing(self):
        self.run_round("2026-01-01")
        del self.doc["pins_max"]
        self.run_round("2026-01-02")
        self.assertEqual(self.slack.pins, set())

    def test_an_unread_history_unpins_what_the_record_says(self):
        self.run_round("2026-01-20")
        history = self.slack.call

        def no_history(method, **p):
            if method == "conversations.history":
                raise Error("Slack conversations.history failed: missing_scope")
            return history(method, **p)

        self.slack.call = no_history
        self.run_round("2026-01-26")
        self.assertEqual(self.pinned_ids(), ["rule"])

    def test_a_record_of_one_pin_from_before_is_read(self):
        self.run_round("2026-01-01")
        [ts] = self.slack.pins
        Path(self.pinned).write_text(json.dumps({"ts": ts}))
        self.assertEqual(notices.pins(self.pinned), {ts})
        self.run_round("2026-01-20")
        self.assertEqual(self.pinned_ids(), ["downtime", "rule"])
        self.assertEqual(set(json.loads(Path(self.pinned).read_text())["pins"]), set(self.slack.pins))


class PinCheck(unittest.TestCase):
    def test_a_pin_the_bot_cannot_read_is_said(self):
        doc = {"pins_max": 0, "notices": [notice("x", "2026-01-01", "soon")]}
        said = "\n".join(notices.check(doc))
        self.assertIn("pins_max 0", said)
        self.assertIn("pin_until 'soon'", said)
        self.assertEqual(notices.check({"pins_max": 3, "notices": [notice("x", "2026-01-01")]}), [])


class Main(Base):
    """The owner's run (python -m cluster_bot.notices CONFIG): it ran silent when the
    channel already matched, which read as if it did nothing at all."""

    def main(self, *args):
        token = os.path.join(self.dir.name, "bot.token")
        Path(token).write_text("xoxb-test")
        cfg = os.path.join(self.dir.name, "config.json")
        Path(cfg).write_text(json.dumps({"bot_token_file": token, "notices": {
            "url": "https://docs.example.org/notices.json", "channel": CHANNEL,
            "seen": self.seen, "pinned": self.pinned}}))  # fmt: skip
        out = []
        with mock.patch.object(notices, "Slack", return_value=self.slack), \
                mock.patch.object(notices, "fetch_json", return_value=self.doc), \
                mock.patch("builtins.print", lambda line, **kw: out.append(line)):  # fmt: skip
            notices.main([cfg, *args])
        return out

    def test_a_run_that_changes_nothing_says_so(self):
        self.run_round("2026-01-01")
        self.run_round()
        out = self.main()
        self.assertEqual(out[-1], "notices: the channel already shows notices.json: nothing changed")
        self.assertTrue(out[0].startswith("notices: missing"), "the notice seeded unposted is said")

    def test_a_run_says_the_posts_it_leaves(self):
        self.run_round("2026-01-01")
        self.run_round()
        self.slack.add("9.0", "theirs", notice("old", "")["blocks"], bot="B9", app="A9")
        out = self.main()
        self.assertTrue(any(line.startswith("notices: foreign 9.0") for line in out), out)


if __name__ == "__main__":
    unittest.main()
