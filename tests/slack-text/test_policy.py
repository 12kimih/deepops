"""What every Slack message the cluster sends holds to (owner, 2026-11-28):

- Its language is its place's (cluster_tools.slack.LANGUAGE): the administrators' channel
  all English, the places people read Korean. A text copied into a log stays as sent.
- An emoji only where cluster_tools.emoji puts one: no large circles, no pointer before
  every step, no icon before a log's title.
"""

import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
sys.path.insert(0, str(ROOT / "roles/cluster_bot/files"))
from cluster_bot import bans, bans_blocks, data_blocks, remove, scopes  # noqa: E402
from cluster_bot import blocks as bot_blocks  # noqa: E402
from cluster_bot.requests import Failed, Outcome  # noqa: E402
from cluster_tools import cards, disk_report, emoji, rich, slack  # noqa: E402
from cluster_tools import usage_report as report  # noqa: E402

HANGUL = re.compile(r"[가-힣]")
# An emoji as Slack reads one in a text: :name:, not inside a word or a metric name
# (job_device:dcgm_gpu_util_ratio:max), the same pattern cluster_tools.rich reads.
EMOJI = re.compile(r"(?<![\w:]):([a-z0-9_+-]*[a-z][a-z0-9_+-]*):(?![\w:])")
CODE = (ROOT / "roles/cluster_tools_lib/files/cluster_tools", ROOT / "roles/cluster_bot/files/cluster_bot")
TEMPLATE = ROOT / "roles/alertmanager/files/slack.tmpl"
# The bot's messages (tests/cluster-bot/test_messages.py) shown in the administrators'
# channel, checked as English below; the rest are read by people.
ADMIN_SHOWN = ("ban list", "new bans", "held, administrators")
BAN = {"ip": "203.0.113.7", "host": "gpu1", "jail": "sshd", "since": "2026-11-25 10:00"}

_spec = importlib.util.spec_from_file_location("bot_messages", ROOT / "tests/cluster-bot/test_messages.py")
bot_messages = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bot_messages)


def shown(message):
    """What MESSAGE shows, as text: blocks, a view, (text, blocks), [(text, blocks)] or a text."""
    if isinstance(message, str):
        return message
    if isinstance(message, tuple):
        return f"{message[0]}\n{shown(message[1])}"
    if isinstance(message, dict):
        return shown(message.get("blocks") or [message])
    if message and isinstance(message[0], tuple):
        return "\n".join(shown(m) for m in message)
    return rich.read(message)


def admin_messages():
    """(name, message) of everything the cluster posts in the administrators' channel."""
    errors = {"gpu1": Failed("the bot's key is not accepted", "run the playbook")}
    yield "ban list", bans_blocks.ban_list([BAN] * 3, errors)
    yield "ban list, none", bans_blocks.ban_list([], {})
    yield "ban list, unread", bans_blocks.ban_list([], errors, read=False)
    yield "new bans", bans_blocks.new_bans([BAN] * 2)
    yield "ban log", bans.log_title("2026-11-28", 2)
    yield "held", bot_blocks.held_admin("alice", "new account bob (team-a)", "k1")
    done = Outcome("done", "x", request={"kind": "join"})
    failed = Outcome("refused", "x", {"error": "GitHub PUT /x: 409 conflict"}, request={"kind": "join"})
    for outcome, approved in ((done, True), (failed, True), (done, False), (Outcome("refused", "x"), True)):
        yield "decided", bot_blocks.decided_admin(outcome, approved, "carol", "new account bob (team-a)")
    yield "scopes lacking", scopes.message(["bookmarks:read", "pins:write"], [], "/etc/cluster-bot/m.json")
    yield "scopes whole", scopes.message([], ["pins:write"], "/etc/cluster-bot/m.json")
    yield "delete log", remove.log_title("2026-11-28", 1)
    yield "delete entry", remove.log_entry("alice", "C1", {"text": "x", "blocks": rich.blocks("x")})
    yield "dm log", slack.dm_log_title("2026-11-28", 3)
    yield "dm log line", slack.dm_log_line("disk", "alice", "https://slack.example/p1")
    yield "repeaters", report.render_repeaters(
        ["bob"], {"bob": [{"idle": 90, "held": 120}] * 2},
        [{"label": "1", "user": "bob", "gpus": [("n1", 0)]}],
        {"repeat_weeks": 2, "repeat_idle_gpu_hours": 50, "repeat_idle_share": 0.5},
    )  # fmt: skip
    yield "scratch refresh", disk_report.burst_text(("n1", "alice"), 300)
    yield "data log", data_blocks.log_title("2026-11-28", 1)
    yield "data log entry", data_blocks.removed_admin("/home/shared/datasets/x", 60, 14)
    yield "data not removed", data_blocks.failed_removal_admin("/home/shared/datasets/x", "SSH failed")


def korean_messages():
    """(name, message) of what people read: the status channel's reports and the bot."""
    yield "weekly", report.render_week([{"user": "alice", "held": 120, "idle": 90}], 5)
    cfg = {"idle_util_threshold": 5, "idle_hours": 2, "cancel_after_hours": 6}
    data = {"jobs": [], "idle": [], "total_gpus": 8, "now": 0, "reasons": [], "pending": {}}
    yield "hourly", report.status_card(data, cfg)
    yield "hourly line", report.hour_line(data, hot=[("n1", ["alice"])])
    yield "hourly details", report.details(data, cfg)
    yield "day summary", report.day_summary("2026-11-28", [report.day_sample(data)])
    for name, message in bot_messages.messages():
        if not name.startswith(ADMIN_SHOWN):
            yield name, message


class Language(unittest.TestCase):
    def test_the_places_languages_are_named_once(self):
        self.assertEqual(slack.LANGUAGE["admin"], "en")
        self.assertEqual({slack.LANGUAGE[r] for r in ("status", "help", "announce", "dm")}, {"ko"})

    def test_the_administrators_channel_is_english(self):
        for name, message in admin_messages():
            with self.subTest(name):
                text = shown(message)
                self.assertEqual(HANGUL.findall(text), [], f"{name}: {text[:300]}")
                self.assertEqual(
                    rich.problems(message if not isinstance(message, str) else rich.blocks(message)), []
                )

    def test_the_places_people_read_are_korean(self):
        for name, message in korean_messages():
            with self.subTest(name):
                self.assertTrue(HANGUL.search(shown(message)), name)

    def test_a_log_mentions_no_one(self):
        """A day log is read when wanted; a mention would ring for an entry no one acts on."""
        for name, message in admin_messages():
            if "log" in name or "entry" in name or name == "new bans":
                with self.subTest(name):
                    self.assertNotRegex(shown(message), r"<[@!]", name)


class Emoji(unittest.TestCase):
    def test_the_code_names_only_the_policys_emoji(self):
        found = []
        for root in CODE:
            for path in sorted(root.glob("*.py")):
                for n, line in enumerate(path.read_text().splitlines(), 1):
                    for m in EMOJI.finditer(line):
                        if f":{m.group(1)}:" not in emoji.ALLOWED and m.group(1) != "emoji":  # rich's grammar
                            found.append(f"{path.relative_to(ROOT)}:{n}: :{m.group(1)}:")
        self.assertEqual(found, [], "choose emoji in cluster_tools.emoji")

    def test_the_alert_template_uses_the_alert_marks(self):
        used = {f":{m}:" for m in EMOJI.findall(TEMPLATE.read_text())}
        self.assertEqual(used, set(emoji.ALERT.values()))

    def test_every_message_shows_only_the_policys_emoji(self):
        messages = [*admin_messages(), *korean_messages()]
        for name, message in messages:
            with self.subTest(name):
                used = {f":{m}:" for m in EMOJI.findall(shown(message))}
                self.assertLessEqual(used, emoji.ALLOWED, name)

    def test_no_large_mark_and_no_pointer(self):
        for e in emoji.ALLOWED:
            self.assertNotRegex(e, r"large_|_circle|point_")

    def test_a_report_title_has_the_report_mark_and_a_step_none(self):
        self.assertEqual(
            cards.title("GPU 사용 현황", "now", report=True)["text"]["text"], ":bar_chart: GPU 사용 현황  now"
        )
        self.assertEqual(cards.title("x")["text"]["text"], "x")
        self.assertEqual(rich.read(cards.step("`usage`로 봅니다.")), "`usage`로 봅니다.")


if __name__ == "__main__":
    unittest.main()
