"""cluster_bot.channels: each channel's guide (purpose, topic, canvas, bookmarks) kept
from the docs site's channels.json; people's own bookmarks and other channels untouched."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_bot/files"))
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))  # cluster_tools, which the bot imports
from cluster_bot import channels, notices  # noqa: E402


class Refused(RuntimeError):
    pass


class FakeSlack:
    """The Web API the guides call, over one workspace's channels."""

    def __init__(self, existing_canvas=None):
        self.chan = {
            "C1": {"name": "news", "topic": "", "purpose": "", "canvas": existing_canvas, "bookmarks": {}},
            "C2": {"name": "ask", "topic": "", "purpose": "", "canvas": None, "bookmarks": {}},
        }
        self.calls, self.canvases, self.pins, self.refuse, self.n = [], {}, {}, set(), 0

    def _id(self, prefix):
        self.n += 1
        return f"{prefix}{self.n}"

    def call(self, method, **p):
        self.calls.append((method, p))
        if method in self.refuse:
            raise Refused(f"Slack {method} failed: not_allowed")
        c = self.chan.get(p.get("channel") or p.get("channel_id"))
        if method == "conversations.list":
            return {"channels": [{"id": i, "name": c["name"]} for i, c in self.chan.items()]}
        if method == "conversations.info":
            props = {"canvas": {"file_id": c["canvas"]}} if c["canvas"] else {}
            return {
                "channel": {
                    "topic": {"value": c["topic"]},
                    "purpose": {"value": c["purpose"]},
                    "properties": props,
                }
            }
        if method == "conversations.setTopic":
            c["topic"] = p["topic"]
        elif method == "conversations.setPurpose":
            c["purpose"] = p["purpose"]
        elif method == "conversations.canvases.create":
            if c["canvas"]:
                raise Refused("Slack conversations.canvases.create failed: channel_canvas_already_exists")
            c["canvas"] = self._id("F")
            self.canvases[c["canvas"]] = json.loads(p["document_content"])["markdown"]
            return {"canvas_id": c["canvas"]}
        elif method == "canvases.edit":
            [change] = json.loads(p["changes"])
            if change["operation"] == "replace":
                self.assertNoSection(change)
                self.canvases[p["canvas_id"]] = change["document_content"]["markdown"]
        elif method == "bookmarks.list":
            return {"bookmarks": list(c["bookmarks"].values())}
        elif method == "bookmarks.add":
            bid = self._id("Bk")
            c["bookmarks"][bid] = {"id": bid, "title": p["title"], "link": p["link"], "emoji": p["emoji"]}
            return {"bookmark": c["bookmarks"][bid]}
        elif method == "bookmarks.edit":
            c["bookmarks"][p["bookmark_id"]].update(link=p["link"], emoji=p["emoji"])
        elif method == "bookmarks.remove":
            del c["bookmarks"][p["bookmark_id"]]
        elif method == "pins.add":
            self.pins.setdefault(p["channel"], set()).add(p["timestamp"])
        elif method == "pins.remove":
            self.pins[p["channel"]].discard(p["timestamp"])
        return {}

    @staticmethod
    def assertNoSection(change):
        assert "section_id" not in change, "a full replace names no section"

    def made(self, method):
        return [p for m, p in self.calls if m == method]


NAMES = {"announce": "#news", "help": "#ask"}


def guide(topic="Questions go to [[#help]]", markdown="# News\n\nAsk in [[#help]].", bookmarks=None):
    return {
        "purpose": "Notices only",
        "topic": topic,
        "canvas": {"title": "About this channel", "markdown": markdown},
        "bookmarks": bookmarks
        if bookmarks is not None
        else [{"title": "Docs", "url": "https://docs.example.org/", "emoji": ":books:"}],
    }


class Guides(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "channel-guides.json")
        self.doc = {"channels": {"announce": guide()}}
        self.log = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_round(self, slack, today="2026-01-01"):
        channels.round_(lambda: self.doc, slack, NAMES, self.state, today, self.log.append)

    def test_a_service_address_is_filled_from_the_config_or_its_line_left_out(self):
        """The public channels.json holds [[url:portal]], never the address."""
        self.doc = {"channels": {"announce": guide(
            markdown="# News\n\n- [Portal]([[url:portal]]): log in\n- [Grafana]([[url:dashboard]]): charts\n",
            bookmarks=[{"title": "Portal", "url": "[[url:portal]]", "emoji": ":globe_with_meridians:"},
                       {"title": "Grafana", "url": "[[url:dashboard]]", "emoji": ":chart_with_upwards_trend:"}])}}  # fmt: skip
        slack = FakeSlack()
        urls = channels.service_urls({"portal_url": "https://portal.example/", "dashboard_url": ""})
        channels.round_(lambda: self.doc, slack, NAMES, self.state, "2026-01-01", self.log.append, urls)
        news = slack.chan["C1"]
        self.assertEqual(
            slack.canvases[news["canvas"]], "# News\n\n- [Portal](https://portal.example/): log in\n"
        )
        self.assertEqual([(b["title"], b["link"]) for b in news["bookmarks"].values()],
                         [("Portal", "https://portal.example/")], "no address: no bookmark")  # fmt: skip
        self.assertNotIn("[[url:", json.dumps(slack.calls))

    def test_a_channel_gets_its_purpose_topic_canvas_and_bookmarks_once(self):
        slack = FakeSlack()
        self.run_round(slack)
        news = slack.chan["C1"]
        self.assertEqual((news["purpose"], news["topic"]), ("Notices only", "Questions go to #ask"))
        self.assertEqual(slack.canvases[news["canvas"]], "# News\n\nAsk in ![](#C2).")
        self.assertEqual([b["title"] for b in news["bookmarks"].values()], ["Docs"])
        self.assertEqual(slack.made("canvases.access.set")[0]["access_level"], "read")
        self.assertEqual(slack.chan["C2"]["topic"], "", "a channel the site does not describe is left alone")
        self.assertEqual(self.log, [f"announce: canvas {news['canvas']} created"])
        slack.calls.clear()
        self.run_round(slack)
        changed = {m for m, _ in slack.calls} - {"conversations.list", "conversations.info", "bookmarks.list"}
        self.assertEqual(changed, set(), "a converged channel changes nothing the same day")

    def test_the_canvas_is_replaced_when_its_text_changes_and_once_a_day(self):
        slack = FakeSlack()
        self.run_round(slack)
        canvas = slack.chan["C1"]["canvas"]
        slack.canvases[canvas] = "a hand edit"
        self.run_round(slack, "2026-01-02")
        self.assertEqual(slack.canvases[canvas], "# News\n\nAsk in ![](#C2).", "the daily replace undoes it")
        self.doc["channels"]["announce"] = guide(markdown="# News v2")
        self.run_round(slack, "2026-01-02")
        self.assertEqual(slack.canvases[canvas], "# News v2")
        self.assertEqual(len(slack.made("conversations.canvases.create")), 1)

    def test_a_canvas_the_bot_did_not_record_is_taken_over(self):
        slack = FakeSlack(existing_canvas="F9")
        self.run_round(slack)
        self.assertEqual(slack.canvases["F9"], "# News\n\nAsk in ![](#C2).")
        self.assertIn("announce: canvas F9 found in the channel, taken over", self.log)

    def test_a_refused_lock_is_logged_and_the_guide_still_kept(self):
        slack = FakeSlack()
        slack.refuse.add("canvases.access.set")
        self.run_round(slack)
        self.assertTrue(slack.chan["C1"]["canvas"])
        self.assertTrue(any("could not be made read-only" in line for line in self.log))

    def test_only_the_bots_own_bookmarks_are_kept(self):
        slack = FakeSlack()
        slack.chan["C1"]["bookmarks"]["P1"] = {"id": "P1", "title": "Mine", "link": "https://x/", "emoji": ""}
        self.run_round(slack)
        self.doc["channels"]["announce"] = guide(
            bookmarks=[{"title": "Docs", "url": "https://docs.example.org/v2/", "emoji": ":books:"},
                       {"title": "News", "url": "https://docs.example.org/news/", "emoji": ":newspaper:"}]
        )  # fmt: skip
        self.run_round(slack)
        marks = {b["title"]: b["link"] for b in slack.chan["C1"]["bookmarks"].values()}
        self.assertEqual(
            marks,
            {
                "Mine": "https://x/",
                "Docs": "https://docs.example.org/v2/",
                "News": "https://docs.example.org/news/",
            },
        )
        self.doc["channels"]["announce"] = guide(bookmarks=[])
        self.run_round(slack)
        self.assertEqual([b["title"] for b in slack.chan["C1"]["bookmarks"].values()], ["Mine"])

    def test_an_unreadable_site_changes_nothing(self):
        slack = FakeSlack()

        def down():
            raise OSError("no route")

        with self.assertRaises(OSError):
            channels.round_(down, slack, NAMES, self.state, "2026-01-01", self.log.append)
        self.assertEqual(slack.calls, [])

    def test_a_channel_the_bot_cannot_see_is_logged_and_the_others_kept(self):
        slack = FakeSlack()
        self.doc["channels"]["help"] = guide()
        names = dict(NAMES, announce="#gone")
        channels.round_(lambda: self.doc, slack, names, self.state, "2026-01-01", self.log.append)
        self.assertIn("announce: #gone: no such channel the bot can see (invite it)", self.log)
        self.assertTrue(slack.chan["C2"]["canvas"])


class PinLatest(unittest.TestCase):
    def test_the_channel_keeps_one_pinned_notice_the_newest(self):
        slack = FakeSlack()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "notices-pinned.json")
            notices.pin_latest(slack, "#news", path, "1.0")
            notices.pin_latest(slack, "#news", path, "2.0")
            self.assertEqual(slack.pins["C1"], {"2.0"})
            slack.pins["C1"].clear()  # someone unpinned it by hand
            notices.pin_latest(slack, "#news", path, "3.0")
            self.assertEqual(slack.pins["C1"], {"3.0"})


if __name__ == "__main__":
    unittest.main()
