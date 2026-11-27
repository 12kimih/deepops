"""cluster_bot.tidy: the owner's tidy of the channels the bot posts in. A survey says who
posted what; of the messages today's code no longer manages (alerts of the retired
webhook, ban notices of the old cut format, notices of a retired integration) the bot's
are deleted with its replies, an integration's are listed with a link to delete by hand
and never tried (Slack's chat.delete acts on the token's bot's messages alone: the fake
refuses the rest as Slack does, tests/lib/fake_slack.py), and an integration's notice
is recorded as announced so the notices round never posts it again."""

import json
import sys
import tempfile
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
RETIRED = ("BOLD", "AOLD")  # an older integration the config knows by name
KNOWN = {RETIRED[0]: "the retired notice webhook"}
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


def run(slack, dry_run, known=KNOWN, seen=None, posted=("a", "b"), seen_path=None):
    ctx = tidy.Context(FakeChannel.BOT, FakeChannel.USER, known, notes(*posted),
                       {f"{SITE}{n}/": {"date": "2026-11-19"} for n in posted} if seen is None else seen,
                       seen_path)  # fmt: skip
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

    def test_the_dry_run_surveys_and_says_what_becomes_of_each_and_changes_nothing(self):
        actions, lines, pauses = run(self.slack, dry_run=True)
        self.assertEqual(self.slack.did("chat.delete"), [])
        self.assertEqual(pauses, [])
        text = "\n".join(lines)
        self.assertIn("bot_id=B9 app_id=A1 subtype=bot_message: 2 messages", text)
        self.assertIn("the app's, not its bot", text)
        self.assertIn("bot_id=BX app_id=AX subtype=bot_message: 1 messages", text)
        self.assertIn("another app's", text)
        self.assertIn("bot_id=BOLD app_id=AOLD: 2 messages", text)
        self.assertIn("the retired notice webhook", text, "a known integration by its name")
        self.assertIn("user=U2: 1 messages", text)
        self.assertEqual(self.doing(actions), self.fates())
        self.assertIn(f"delete {self.bans}", text)
        self.assertIn("with 1 replies of the bot; 1 replies of others stay", text)

    def doing(self, actions):
        return {a["ts"]: a["do"] for a in actions}

    def fates(self):
        hand = (self.webhook, self.webhook_old, self.foreign_alert, self.retired_a, self.retired_b,
                self.other_notice)  # fmt: skip
        return {self.bans: "delete", self.person: "left", **{ts: "by hand" for ts in hand}}

    def test_the_real_run_decides_the_same_and_tries_only_the_bots_own(self):
        dry, _, _ = run(self.slack, dry_run=True)
        actions, lines, pauses = run(self.slack, dry_run=False)
        self.assertEqual(self.doing(dry), self.fates())
        self.assertEqual(self.doing(actions), self.fates(), "decided before acting, alike in both runs")
        tried = [ts for _, ts in self.slack.did("chat.delete")]
        self.assertEqual(tried, [self.bans, self.reply_own], "the parent first; nothing else tried")
        kept = {m["ts"] for m in self.slack.messages}
        for ts in (self.bot_alert, self.new_bans, self.reply_person, self.person, self.webhook,
                   self.foreign_alert, self.retired_a, self.bot_notice, self.other_notice):  # fmt: skip
            self.assertIn(ts, kept)
        self.assertEqual(pauses, [tidy.PAUSE] * 2)
        self.assertTrue(next(a for a in actions if a["ts"] == self.bans).get("done"))

    def test_an_integrations_messages_are_listed_once_with_a_link_each(self):
        actions, lines, _ = run(self.slack, dry_run=False)
        heads = [line for line in lines if "by hand in the Slack client" in line]
        self.assertEqual(heads, ["  by hand in the Slack client (an admin), 3 messages:",
                                 "  by hand in the Slack client (an admin), 3 messages:"])  # fmt: skip
        for a in actions:
            if a["do"] == "by hand":
                self.assertTrue(a["link"].endswith(f"p{a['ts']}"), a)
                self.assertTrue(any(line.startswith(f"    {a['link']}  by hand {a['ts']}") for line in lines))

    def test_a_refused_delete_is_said_and_left_and_its_replies_stay(self):
        self.slack.refuse.add(self.bans)
        actions, lines, _ = run(self.slack, dry_run=False)
        kept = {m["ts"] for m in self.slack.messages}
        self.assertIn(self.bans, kept)
        self.assertIn(self.reply_own, kept)
        self.assertTrue(any("cant_delete_message" in line and "by hand" in line for line in lines))
        self.assertFalse(next(a for a in actions if a["ts"] == self.bans).get("done"))

    def test_an_integrations_notice_is_recorded_once_and_never_posted_again(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "notices-seen.json")
            seen = {f"{SITE}a/": {"date": "2026-11-19", "ts": "1.0"}}  # b was never recorded
            path.write_text(json.dumps(seen))
            _, lines, _ = run(self.slack, dry_run=True, seen=seen, seen_path=str(path))
            self.assertEqual(json.loads(path.read_text()), seen, "the dry run records nothing")
            self.assertIn(f"  would record {SITE}b/ as announced: an integration's copy stands ({self.retired_b})",
                          lines)  # fmt: skip
            run(self.slack, dry_run=False, seen=seen, seen_path=str(path))
            recorded = json.loads(path.read_text())
            self.assertEqual(set(recorded), {f"{SITE}a/", f"{SITE}b/"})
            self.assertNotIn("ts", recorded[f"{SITE}b/"])
            self.assertEqual(recorded[f"{SITE}b/"]["digest"], notes("a", "b")[f"{SITE}b/"]["digest"])
            for still in (True, False):  # the copy still there, then deleted by hand
                left = [m for m in self.slack.posts("CNEWS") if still or m["ts"] != self.retired_b]
                plan = notices.plan(left, notes("a", "b"), recorded, FakeChannel.BOT, "2026-11-27",
                                    adopt=True, most=3)  # fmt: skip
                about_b = [a["do"] for a in plan if a.get("id") == f"{SITE}b/"]
                self.assertNotIn("post", about_b)
                self.assertNotIn("pin", about_b, "a notice without a post is never pinned")
                if not still:
                    self.assertIn("missing", about_b)
            plan = notices.plan(
                [], notes("a", "b"), recorded, FakeChannel.BOT, "2026-11-27", adopt=True, most=3
            )
            self.assertNotIn(
                ("pin", f"{SITE}b/"), [(a["do"], a.get("id")) for a in plan], "nor by the record"
            )

    def test_every_line_names_its_message(self):
        actions, lines, _ = run(self.slack, dry_run=True)
        self.assertTrue(actions)
        for a in actions:
            self.assertTrue(a["text"], a)
            self.assertNotIn(" '':", tidy.describe(a))
        self.assertEqual(next(a for a in actions if a["ts"] == self.webhook)["text"], "[FIRING:1] DiskFull")
        self.assertEqual(next(a for a in actions if a["ts"] == self.bans)["text"], "*새로 차단된 주소 57개*")

    def test_every_poster_line_names_its_newest_message(self):
        _, lines, _ = run(self.slack, dry_run=True)
        posters = [line for line in lines if " messages, " in line and line.startswith("  ")]
        self.assertTrue(posters)
        for line in posters:
            self.assertRegex(line, r", newest: '[^']+'$")
        self.assertTrue(any(line.startswith("  bot_id=B9 ") and line.endswith("newest: ':red_circle: Critical: NodeDown'")
                            for line in posters), posters)  # fmt: skip

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
        ctx = tidy.Context(FakeChannel.BOT, "", {}, {}, {})
        self.assertEqual(
            [a for a in tidy.plan("admin", self.slack.posts("CALERTS"), ctx) if a["what"] == "an alert"], []
        )

    def test_every_run_ends_saying_what_it_did_or_that_nothing_changed(self):
        actions, _, _ = run(self.slack, dry_run=True)
        self.assertEqual(
            tidy.summary(actions, True),
            "would delete 1 messages, 6 to delete by hand, 1 left; nothing changed",
        )
        self.slack.refuse.add(self.bans)
        actions, _, _ = run(self.slack, dry_run=False)
        self.assertEqual(
            tidy.summary(actions, False), "nothing deleted: 1 to delete, 6 to delete by hand, 1 left"
        )
        self.slack.refuse.clear()
        actions, _, _ = run(self.slack, dry_run=False)
        self.assertEqual(tidy.summary(actions, False), "deleted 1 of 1 messages, 6 to delete by hand, 1 left")

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
    def test_the_bot_config_names_the_channels_and_the_known_integrations(self):
        sys.path.insert(0, str(ROOT / "tests/lib"))
        from ansible_yaml import load

        cfg = load("roles/cluster_bot/vars/main.yml")["_cluster_bot_cfg"]
        self.assertEqual(cfg["tidy"]["channels"], "{{ slack_channels }}")
        self.assertIn("cluster_bot_known_integrations", cfg["tidy"]["known"])
        self.assertEqual(load("roles/cluster_bot/defaults/main.yml")["cluster_bot_known_integrations"], {})
        retired = load("roles/retired_settings/vars/main.yml")["retired_settings"]
        self.assertIn("cluster_bot_known_integrations", retired["cluster_bot_retired_bot_ids"])

    def test_main_refuses_a_config_without_channels(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "config.json")
            path.write_text(json.dumps({"bot_token_file": str(Path(d, "t"))}))
            with self.assertRaises(SystemExit):
                tidy.main([str(path), "--dry-run"])


if __name__ == "__main__":
    unittest.main()
