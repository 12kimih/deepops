"""cluster_bot.remove: the message shortcut by which an administrator deletes one of the
bot's messages. Only a member of the administrators' user group (read live), only what
Slack lets the bot delete (its own, the rule tidy decides by), never what a feature keeps
up to date (it would come back), every delete logged like the DMs (one day message in
the administrators' channel, each delete whole in its thread, naming who as text, never
mentioning); through the fake Slack, which refuses another's message as Slack does."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_bot/files"))
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
sys.path.insert(0, str(ROOT / "tests/lib"))
from cluster_bot import commands, remove  # noqa: E402
from cluster_tools import rich  # noqa: E402
from cluster_tools.slack import KEPT, marked  # noqa: E402
from fake_slack import FakeChannel  # noqa: E402

GROUP, ADMIN, OTHER = "S0ADMINS", "U0ADMIN", "U0OTHER"
WEBHOOK = ("B9", "A1")  # the app's incoming webhook: its own bot_id, the app's app_id
KNOWN = {"BOLD": "the retired notice webhook"}


class Shortcut(unittest.TestCase):
    def setUp(self):
        self.slack = FakeChannel()
        self.slack.usergroups[GROUP] = [ADMIN]
        self.mine = self.slack.add("이번 주 사용량", "#news")
        self.reply = self.slack.add("자세히", "#news", thread_ts=self.mine)
        self.person_reply = self.slack.add(
            "고맙습니다", "#news", bot=("", ""), user="U3", thread_ts=self.mine
        )

    def run_on(self, ts, user=ADMIN, group=GROUP, thread_ts=None):
        return remove.run(self.slack, user=user, name="admin1", channel="CNEWS", ts=ts, thread_ts=thread_ts,
                          group_id=group, known=KNOWN, deletes=remove.log("CADMIN"))  # fmt: skip

    def day_messages(self):
        return [m for m in self.slack.posts("CADMIN") if (m.get("metadata") or {}).get("event_type") == KEPT]

    def kept(self):
        return {m["ts"] for m in self.slack.messages}

    def test_an_administrator_deletes_the_bots_message_with_its_replies_and_it_is_logged(self):
        self.assertEqual(self.run_on(self.mine), remove.DONE)
        self.assertNotIn(self.mine, self.kept())
        self.assertNotIn(self.reply, self.kept())
        self.assertIn(self.person_reply, self.kept(), "a person's reply stays")
        [day] = self.day_messages()
        self.assertTrue(day["text"].startswith("Deleted bot messages "), day["text"])
        [entry] = self.slack.thread(day["ts"])
        shown = rich.read(entry["blocks"])
        self.assertIn("admin1 deleted a bot message in <#CNEWS>", shown)
        self.assertIn("이번 주 사용량", shown, "the message as it was")
        self.assertNotIn("<@", entry["text"] + shown + day["text"], "a log mentions no one")

    def test_deletes_of_one_day_share_one_message_and_its_count(self):
        other = self.slack.add("공지", "#news")
        self.run_on(self.mine)
        self.run_on(other)
        [day] = self.day_messages()
        self.assertEqual(len(self.slack.thread(day["ts"])), 2)
        self.assertIn(": 2 ", day["text"])

    def test_one_reply_of_the_bot_goes_alone(self):
        self.assertEqual(self.run_on(self.reply, thread_ts=self.mine), remove.DONE)
        self.assertEqual(self.kept() & {self.mine, self.reply}, {self.mine})

    def test_only_a_member_of_the_administrators_group_may(self):
        self.assertEqual(self.run_on(self.mine, user=OTHER), remove.NOT_ADMIN)
        self.assertEqual(self.run_on(self.mine, group=""), remove.NO_GROUP)
        self.assertIn(self.mine, self.kept())
        self.assertEqual(self.slack.did("chat.delete"), [])

    def test_another_posters_message_is_refused_saying_who_and_never_tried(self):
        webhook = self.slack.add("[FIRING:1] DiskFull", "#news", bot=WEBHOOK)
        known = self.slack.add("old notice", "#news", bot=("BOLD", "AOLD"))
        person = self.slack.add("hello", "#news", bot=("", ""), user="U4")
        for ts, who in ((webhook, "봇이 아닌 연동"), (known, "the retired notice webhook"), (person, "사람")):
            with self.subTest(who=who):
                said = self.run_on(ts)
                self.assertIn("봇이 지울 수 없습니다", said)
                self.assertIn(who, said)
        self.assertEqual(self.slack.did("chat.delete"), [], "never tried: Slack would refuse")

    def test_a_message_a_feature_keeps_up_to_date_is_refused(self):
        kept = self.slack.add("차단 목록", "#news", metadata=marked("bans list"))
        self.assertEqual(self.run_on(kept), remove.KEPT_TEXT)
        self.assertIn(kept, self.kept())

    def test_a_message_already_gone_is_said(self):
        self.assertEqual(self.run_on("9999.0"), remove.GONE)


class Manifest(unittest.TestCase):
    def test_the_manifest_declares_the_shortcut_the_app_handles(self):
        [shortcut] = commands.manifest("/cluster")["features"]["shortcuts"]
        self.assertEqual((shortcut["type"], shortcut["callback_id"]), ("message", remove.SHORTCUT))
        self.assertIn(
            "app.shortcut(remove.SHORTCUT)", (ROOT / "roles/cluster_bot/files/cluster_bot/app.py").read_text()
        )
        self.assertIn("usergroups:read", commands.manifest("/cluster")["oauth_config"]["scopes"]["bot"])


if __name__ == "__main__":
    unittest.main()
