"""No tool may flood a channel (owner, 2026-11-28: "메시지가 넘치지 않도록, 과다로 올라올
수 있는 상황 모두 검사하고 다시 발생하지 않게"). Every channel post goes through
cluster_tools.slack.Slack.post: a top-level one says what it is (CLASSES) or is refused,
each is counted by channel role, class and place, and past POST_BUDGET top-level posts
of the bot in a channel within the hour the rest go in one message's thread. The code
holds to it at every call site."""

import ast
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
sys.path.insert(0, str(ROOT / "tests/lib"))
from cluster_tools import prom, rich, slack  # noqa: E402
from fake_slack import FakeChannel  # noqa: E402
from repo_scan import tracked  # noqa: E402

CODE = [p for p in tracked("roles/*/files/**/*.py", "scripts/**/*.py") if p.suffix == ".py"]


class Poster(slack.Slack):
    """The real Slack.post over a FakeChannel."""

    def __init__(self, channel, **kw):
        super().__init__("xoxb-test", **kw)
        self.ch = channel

    def call(self, method, **params):
        return self.ch.call(method, **params)

    def _send(self, channel, msg, thread_ts=None, metadata=None):
        return self.ch.wire(channel, msg["text"], msg["blocks"], thread_ts, metadata)


class Posts(unittest.TestCase):
    def setUp(self):
        self.ch = FakeChannel()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.textfile = os.path.join(tmp.name, "slack.prom")
        self.slack = Poster(self.ch, counts=slack.DmCounts(self.textfile, "test"),
                            places={"#admin": "admin", "#status": "status"})  # fmt: skip

    def counted(self):
        return {tuple(sorted(dict(labels).items())): float(v) for (name, labels), v in
                prom.parse(Path(self.textfile).read_text()).items() if name == "slack_posts_total"}  # fmt: skip

    def test_a_top_level_post_says_what_it_is_a_reply_need_not(self):
        with self.assertRaisesRegex(slack.Error, "says what it is"):
            self.slack.post("#admin", "x")
        with self.assertRaisesRegex(slack.Error, "says what it is"):
            self.slack.post("#admin", "x", cls="chatter")
        ts = self.slack.post("#admin", "x", cls="notice")
        self.slack.post("#admin", "y", thread_ts=ts)
        self.assertEqual(len(self.ch.posts("CADMIN")), 1)
        self.assertEqual(len(self.ch.thread(ts)), 1)

    def test_each_post_is_counted_by_role_class_and_place(self):
        ts = self.slack.post("#admin", "x", cls="notice")
        self.slack.post("#admin", "y", thread_ts=ts)
        self.slack.post("#status", "z", cls="report")
        self.slack.post("#other", "w", cls="answer")
        got = self.counted()
        base = (("sender", "test"),)

        def at(role, cls, place):
            return tuple(sorted((*base, ("channel_role", role), ("class", cls), ("place", place))))

        self.assertEqual(got[at("admin", "notice", "top")], 1)
        self.assertEqual(got[at("admin", "reply", "thread")], 1)
        self.assertEqual(got[at("status", "report", "top")], 1)
        self.assertEqual(got[at("other", "answer", "top")], 1, "a channel not in places")

    def test_past_the_budget_the_rest_of_the_hour_goes_in_one_thread(self):
        for i in range(slack.POST_BUDGET):
            self.slack.post("#admin", f"notice {i}", cls="notice")
        a = self.slack.post("#admin", "one more", cls="notice")
        b = self.slack.post("#admin", "and another", cls="notice")
        top = self.ch.posts("CADMIN")
        self.assertEqual(len(top), slack.POST_BUDGET + 1, "one fold message, no more top-level posts")
        fold = top[-1]
        self.assertIn("More than 20 messages within the hour", fold["text"])
        self.assertEqual([m["ts"] for m in self.ch.thread(fold["ts"])], [a, b])
        self.assertEqual(sum(v for k, v in self.counted().items() if ("place", "folded") in k), 2)

    def test_the_fold_speaks_its_channels_language(self):
        for i in range(slack.POST_BUDGET + 1):
            self.slack.post("#status", f"report {i}", cls="report")
        self.assertIn("한 시간에", self.ch.posts("CSTATUS")[-1]["text"])

    def test_a_day_log_is_never_folded_and_no_history_never_stops_a_post(self):
        for i in range(slack.POST_BUDGET):
            self.slack.post("#admin", f"notice {i}", cls="notice")
        self.slack.post("#admin", "DM log", cls="log")
        self.assertEqual(self.ch.posts("CADMIN")[-1]["text"], "DM log")
        blind = Poster(FakeChannel(no_history=True))
        for i in range(slack.POST_BUDGET + 2):
            blind.post("#admin", f"notice {i}", cls="notice")
        self.assertEqual(len(blind.ch.posts("CADMIN")), slack.POST_BUDGET + 2, "counted, not folded")

    def test_every_class_and_the_folds_text_are_named(self):
        self.assertEqual(set(slack.CLASSES), {"kept", "log", "report", "notice", "answer"})
        self.assertEqual(set(slack.FOLD_TEXT), set(slack.LANGUAGE.values()))
        for text in slack.FOLD_TEXT.values():
            self.assertEqual(rich.problems(rich.blocks(text.format(n=1))), [])


# A post sent around Slack.post: Bolt's client method, or the API method called by name
# (a scope list naming the method is no call).
AROUND = re.compile(r"\bchat_postMessage\(|\bcall\(\s*\"chat\.postMessage\"")


def calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


class CallSites(unittest.TestCase):
    """The rule holds at the source: a tool cannot post around it."""

    def test_every_post_declares_a_class_or_is_a_reply(self):
        found = []
        for path in CODE:
            for node in calls(ast.parse(path.read_text())):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "post" and node.args:
                    words = {k.arg for k in node.keywords}
                    if not words & {"cls", "thread_ts"} and not any(k.arg is None for k in node.keywords):
                        found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(found, [], "post(..., cls=<one of cluster_tools.slack.CLASSES>) or a reply")

    def test_nothing_posts_around_cluster_tools_slack(self):
        """slack_bolt's client.chat_postMessage, or the method called by name, skips the
        class, the count and the fold."""
        found = []
        for path in CODE:
            if path.name == "slack.py" and path.parent.name == "cluster_tools":
                continue  # Slack._send itself
            found += [f"{path.relative_to(ROOT)}: {m.group(0)}" for m in AROUND.finditer(path.read_text())]
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
