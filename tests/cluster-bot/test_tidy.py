"""cluster_bot.tidy: the owner's tidy of the channels the bot posts in. A survey says who
posted what; the messages today's code no longer manages (alerts of the retired webhook,
ban notices of the old cut format, notices of a retired integration) are deleted, with
the app's replies under them, and only when they are the app's. Before, they stood for
good: nothing deleted what no round of today's code manages."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_bot/files"))
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))  # cluster_tools, which the bot imports
from cluster_bot import notices, tidy  # noqa: E402
from cluster_tools.slack import mentioning, unmentioning  # noqa: E402

sys.path.insert(0, str(ROOT / "tests/lib"))
from fake_slack import FakeChannel  # noqa: E402

ALERTS, NEWS = "#alerts", "#news"
CHANNELS = {"admin": ALERTS, "announce": NEWS, "help": "#help"}
WEBHOOK = ("B9", "A1")  # the app's incoming webhook: its own bot_id, the app's app_id
RETIRED = ("BOLD", "AOLD")  # an older integration the config names
OTHER = ("BX", "AX")  # another app
SITE = "https://docs.example.org/news/"
ADMINS = "<!subteam^S0123ABCD>"  # the admin mention bans.py puts first


def alert(slack, title="[FIRING:1] DiskFull", bot=WEBHOOK, footer=None):
    """An Alertmanager message: through the webhook (subtype bot_message), or the bot's. As
    Slack shows a webhook's, its text is empty: what it says is in the attachment."""
    attachment = {"title": title, "fallback": title, **({"footer": footer} if footer else {})}
    shown = {} if bot == FakeChannel.BOT else {"subtype": "bot_message"}
    return slack.add("", ALERTS, bot=bot, attachments=[attachment], **shown)


def old_bans(slack, n=57, shown=40):
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*새로 차단된 주소 {n}개*"}},
              {"type": "section", "text": {"type": "mrkdwn", "text": f"`192.0.2.1` h (sshd)\n외 {n - shown}개"}}]  # fmt: skip
    # As bans.py posted it: the admin mention first (cluster_tools.slack.mentioning).
    text, blocks = mentioning(ADMINS, f"새로 차단된 주소 {n}개", blocks)
    return slack.add(text, ALERTS, blocks=blocks)


def notice_post(slack, nid, bot=RETIRED):
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": nid}},
              {"type": "actions", "elements": [{"type": "button", "url": f"{SITE}{nid}/"}]}]  # fmt: skip
    return slack.add(nid, NEWS, bot=bot, blocks=blocks)


def notes(*nids):
    """The notices as the bot reads them (notices.notices_of), never a hand copy of its shape."""
    return notices.notices_of({"notices": [{"id": f"{SITE}{n}/", "date": "2026-11-19", "text": n, "blocks": [
        {"type": "header", "text": {"type": "plain_text", "text": n}}]} for n in nids]})  # fmt: skip


def run(slack, dry_run, retired=(RETIRED[0],), seen=None, posted=("a", "b")):
    ctx = tidy.Context(FakeChannel.BOT, FakeChannel.USER, retired, notes(*posted),
                       {f"{SITE}{n}/": {"date": "2026-11-19"} for n in posted} if seen is None else seen)  # fmt: skip
    lines, pauses = [], []
    actions = tidy.tidy(slack, CHANNELS, ctx, lines.append, dry_run=dry_run, pause=pauses.append)
    return actions, lines, pauses


class Tidy(unittest.TestCase):
    def setUp(self):
        self.slack = FakeChannel()
        s = self.slack
        self.webhook = alert(s)
        self.webhook_old = alert(s, title=":red_circle: Critical: NodeDown", footer="Alertmanager")
        self.bot_alert = alert(s, title=":red_circle: Critical: NodeDown", footer="Alertmanager",
                               bot=FakeChannel.BOT)  # fmt: skip
        self.bans = old_bans(s)
        self.new_bans = s.add("새로 차단된 주소 3개", ALERTS, blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": "*새로 차단된 주소 3개*\n호스트별: h 3개"}}])  # fmt: skip
        self.reply_own = s.add("전체 목록", ALERTS, thread_ts=self.bans)
        self.reply_person = s.add("thanks", ALERTS, bot=("", ""), user="U1", thread_ts=self.bans)
        self.person = s.add("[FIRING:1] pasted by a person", ALERTS, bot=("", ""), user="U2",
                            attachments=[{"title": "[FIRING:1] X"}])  # fmt: skip
        self.foreign_alert = alert(s, bot=OTHER)
        self.retired_a = notice_post(s, "a")
        self.retired_b = notice_post(s, "b")
        self.bot_notice = notice_post(s, "a", bot=FakeChannel.BOT)
        self.other_notice = notice_post(s, "b", bot=OTHER)
        s.add("a question", "#help", bot=("", ""), user="U3")

    def deleted(self):
        return {ts for kind, ts in self.slack.did("chat.delete")}

    def test_the_dry_run_surveys_and_says_each_delete_and_changes_nothing(self):
        actions, lines, pauses = run(self.slack, dry_run=True)
        self.assertEqual(self.deleted(), set())
        self.assertEqual(pauses, [])
        text = "\n".join(lines)
        self.assertIn("bot_id=B9 app_id=A1 subtype=bot_message: 2 messages", text)
        self.assertIn("bot_id=BX app_id=AX subtype=bot_message: 1 messages", text)
        self.assertIn("user=U2: 1 messages", text)
        self.assertIn("not the app's", text)
        doing = {a["ts"]: a["do"] for a in actions}
        self.assertEqual(
            doing,
            {self.webhook: "delete", self.webhook_old: "delete", self.bans: "delete",
             self.person: "left", self.foreign_alert: "left", self.retired_a: "delete", self.retired_b: "delete",
             self.other_notice: "left"},
        )  # fmt: skip
        self.assertIn(f"delete {self.bans}", text)
        self.assertIn("with 1 replies of the app; 1 replies of others stay", text)

    def test_it_deletes_only_the_apps_with_their_replies_and_paces_the_calls(self):
        actions, lines, pauses = run(self.slack, dry_run=False)
        gone = {self.webhook, self.webhook_old, self.bans, self.reply_own, self.retired_a, self.retired_b}
        self.assertEqual(self.deleted(), gone)
        kept = {m["ts"] for m in self.slack.messages}
        for ts in (self.bot_alert, self.new_bans, self.reply_person, self.person, self.foreign_alert,
                   self.bot_notice, self.other_notice):  # fmt: skip
            self.assertIn(ts, kept)
        self.assertEqual(pauses, [tidy.PAUSE] * len(gone))
        order = [ts for _, ts in self.slack.did("chat.delete")]
        self.assertLess(order.index(self.bans), order.index(self.reply_own), "the parent first")
        self.assertTrue(all(a.get("done") for a in actions if a["do"] == "delete"))

    def test_a_refused_delete_is_said_and_left_and_its_replies_stay(self):
        self.slack.refuse.add(self.bans)
        actions, lines, _ = run(self.slack, dry_run=False)
        kept = {m["ts"] for m in self.slack.messages}
        self.assertIn(self.bans, kept)
        self.assertIn(self.reply_own, kept)
        self.assertTrue(any("cant_delete_message" in line and "by hand" in line for line in lines))
        self.assertNotIn(self.webhook, kept, "the others still go")
        self.assertFalse(next(a for a in actions if a["ts"] == self.bans).get("done"))

    def test_a_retired_bot_the_config_does_not_name_is_left(self):
        actions, _, _ = run(self.slack, dry_run=False, retired=())
        kept = {m["ts"] for m in self.slack.messages}
        self.assertIn(self.retired_a, kept)
        self.assertEqual(next(a for a in actions if a["ts"] == self.retired_a)["do"], "left")

    def test_a_notice_the_round_would_post_again_stays(self):
        seen = {f"{SITE}a/": {"date": "2026-11-19"}}  # b was never recorded
        actions, _, _ = run(self.slack, dry_run=False, seen=seen)
        kept = {m["ts"] for m in self.slack.messages}
        self.assertNotIn(self.retired_a, kept)
        self.assertIn(self.retired_b, kept)
        left = next(a for a in actions if a["ts"] == self.retired_b)
        self.assertEqual(left["do"], "left")
        self.assertIn("post it again", left["why"])

    def test_the_notices_round_says_missing_not_post_for_a_deleted_retired_notice(self):

        run(self.slack, dry_run=False)
        seen = {f"{SITE}{n}/": {"date": "2026-11-19"} for n in ("a", "b")}
        left = [m for m in self.slack.posts("CNEWS")]
        actions = notices.plan(left, notes("a", "b"), seen, FakeChannel.BOT, "2026-11-27", adopt=True)
        self.assertNotIn("post", [a["do"] for a in actions])
        self.assertIn(("missing", f"{SITE}b/"), [(a["do"], a["id"]) for a in actions])

    def test_every_line_names_its_message(self):
        actions, lines, _ = run(self.slack, dry_run=True)
        self.assertTrue(actions)
        for a in actions:
            self.assertTrue(a["text"], a)
            self.assertNotIn(" '':", tidy.describe(a))
        self.assertEqual(next(a for a in actions if a["ts"] == self.webhook)["text"], "[FIRING:1] DiskFull")
        self.assertEqual(next(a for a in actions if a["ts"] == self.bans)["text"], "*새로 차단된 주소 57개*")

    def test_the_title_reads_blocks_then_attachments_then_text(self):
        self.assertEqual(tidy.title({"text": "t", "attachments": [{"fallback": "f"}]}), "f")
        self.assertEqual(tidy.title({"text": "t", "attachments": [{"title": "a", "fallback": "f"}]}), "a")
        self.assertEqual(tidy.title({"text": f"{ADMINS} t"}), "t")
        self.assertEqual(tidy.title({"text": "", "blocks": [{"type": "header", "text": {"text": "h"}}]}), "h")
        self.assertEqual(tidy.title({}), "")

    def test_every_rule_names_a_channel_role_and_why(self):
        for rule in tidy.RULES:
            self.assertIn(rule.channel, ("status", "admin", "help", "announce"))
            self.assertTrue(rule.what and rule.why and callable(rule.match))

    def test_with_the_bot_user_unknown_no_alert_is_taken_for_the_webhooks(self):
        ctx = tidy.Context(FakeChannel.BOT, "", (), {}, {})
        self.assertEqual(
            [a for a in tidy.plan("admin", self.slack.posts("CALERTS"), ctx) if a["what"] == "an alert"], []
        )

    def test_every_run_ends_saying_what_it_did_or_that_nothing_changed(self):
        actions, _, _ = run(self.slack, dry_run=True)
        self.assertIn("nothing changed", tidy.summary(actions, True))
        self.slack.refuse.update(a["ts"] for a in actions)
        actions, _, _ = run(self.slack, dry_run=False)
        self.assertTrue(tidy.summary(actions, False).startswith("nothing changed: 5 to delete, 3 left"))
        self.slack.refuse.clear()
        actions, _, _ = run(self.slack, dry_run=False)
        self.assertEqual(tidy.summary(actions, False), "deleted 5 of 5 messages, left 3")

    def test_an_unreadable_history_is_said(self):
        slack = FakeChannel(no_history=True)
        for channel in CHANNELS.values():
            slack.add("x", channel)
        _, lines, _ = run(slack, dry_run=True)
        self.assertTrue(any("not readable" in line for line in lines))


class Unmentioning(unittest.TestCase):
    def test_it_is_the_inverse_of_mentioning(self):
        for who in (ADMINS, "<@U0123ABCD>", "<!here>", "<!channel>", "<!subteam^S0123ABCD|admins>"):
            with self.subTest(who=who):
                self.assertEqual(
                    unmentioning(mentioning(who, "새로 차단된 주소 3개")[0]), (who, "새로 차단된 주소 3개")
                )
        self.assertEqual(unmentioning(mentioning("", "x")[0]), ("", "x"))
        self.assertEqual(
            unmentioning("<https://example.org|a link> x"), ("", "<https://example.org|a link> x")
        )


class Config(unittest.TestCase):
    def test_the_bot_config_names_the_channels_and_the_retired_integrations(self):
        sys.path.insert(0, str(ROOT / "tests/lib"))
        from ansible_yaml import load

        cfg = load("roles/cluster_bot/vars/main.yml")["_cluster_bot_cfg"]
        self.assertEqual(cfg["tidy"]["channels"], "{{ slack_channels }}")
        self.assertIn("cluster_bot_retired_bot_ids", cfg["tidy"]["retired"])
        self.assertEqual(load("roles/cluster_bot/defaults/main.yml")["cluster_bot_retired_bot_ids"], [])

    def test_main_refuses_a_config_without_channels(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "config.json")
            path.write_text(json.dumps({"bot_token_file": str(Path(d, "t"))}))
            with self.assertRaises(SystemExit):
                tidy.main([str(path), "--dry-run"])


if __name__ == "__main__":
    unittest.main()
