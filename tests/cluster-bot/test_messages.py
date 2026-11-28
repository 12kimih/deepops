"""Every message the bot sends, rendered as Slack gets it, shows no Markdown marker: bold
right before a Korean particle ("**3개**의") is a style, never stars (cluster_tools.rich.problems).
Before, each module wrote mrkdwn by hand, and Slack showed "*N개*의" as typed."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_bot/files"))
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))  # cluster_tools, which the bot imports
from cluster_bot import (  # noqa: E402
    accounts_blocks,
    bans_blocks,
    blocks,
    data_blocks,
    home_blocks,
    onboarding,
    projects_blocks,
)
from cluster_bot.requests import Failed  # noqa: E402
from cluster_tools import rich  # noqa: E402
from cluster_tools.slack import mentioning  # noqa: E402

CONNECT = {"login_host": "login.example.org", "ssh_port": 2222, "portal_url": "https://portal.example.org/",
           "docs_url": "https://docs.example.org/"}  # fmt: skip
BAN = {"ip": "203.0.113.7", "host": "gpu1", "jail": "sshd", "since": "2026-11-25 10:00"}
PROJECT = {
    "owners": ["alice"],
    "members": ["bob_k"],
    "viewers": [],
    "purpose": "**비전** 모델_학습 [초안](x)",
}


def messages():
    """(name, message) for each thing the bot shows: blocks, views, (text, blocks), texts."""
    errors = {
        "gpu1": Failed("the bot's key is not accepted", "run the playbook"),
        "gpu2": Failed("timed out"),
    }
    for name, (card, thread) in (
        ("ban list", bans_blocks.ban_list([BAN] * 3, errors)),
        ("ban list, none", bans_blocks.ban_list([], {})),
    ):
        yield name, card
        for i, page in enumerate(thread):
            yield f"{name}, reply {i + 1}", page
    for i, page in enumerate(bans_blocks.new_bans([BAN] * 2)):
        yield f"new bans, reply {i + 1}", page
    yield "unban form", bans_blocks.unban_form()
    yield "join form", accounts_blocks.join_form(["team-a"])
    yield "leave form", accounts_blocks.leave_form("alice", True, "2026-11-25")
    yield (
        "leave reminder",
        accounts_blocks.leave_reminder({"ends": "2026-12-01", "login_expires": "2026-12-31"}, 7),
    )
    yield "held", ("요청", blocks.held("<@U1>", "**3개**의 요청", "k1"))
    yield "held, administrators", blocks.held_admin("alice", "new account bob (team-a)", "k1")
    yield "mentioning", mentioning("<!subteam^SADMINS>", "알림", rich.blocks("**2개**의 잡"))
    for muted in (True, False):
        yield "home", home_blocks.home("alice", muted, {"project", "join"}, "/cluster", "<#C1>", "<!subteam^S1>",
                                       web=[("포털", "https://portal.example.org/")])  # fmt: skip
    yield (
        "projects",
        {"type": "home", "blocks": projects_blocks.home("alice", {"vision": PROJECT}, "/home/shared")},
    )
    yield "projects, none", {"type": "home", "blocks": projects_blocks.home("carol", {}, "/home/shared")}
    yield "new project", projects_blocks.new_form({"home": "/home/shared", "scratch": "/scratch",
                                                   "scratch_stale_days": 60})  # fmt: skip
    yield "data notice", data_blocks.notice_dm("/home/shared/data/x", 60, 0)
    texts = {
        "welcome": onboarding.welcome(CONNECT, "/cluster", "alice"),
        "join": onboarding.join_text(CONNECT, "/cluster"),
        "project notice": projects_blocks.notice("vision", "members", "owners", "/home/shared"),
        "data ended": data_blocks.ended("failed", "/home/shared/data/x", {"error": "**없음**"}),
        "data removed": data_blocks.removed("/home/shared/data/x", 60, 14),
        "help": home_blocks.help_text("<#C1>", "<!subteam^S1>"),
    }
    for name, text in texts.items():
        msg = rich.message(text)
        yield name, (msg["text"], msg["blocks"])


class Messages(unittest.TestCase):
    def test_no_message_shows_a_marker(self):
        found = [p for name, message in messages() for p in rich.problems(message, name)]
        self.assertEqual(found, [])

    def test_the_check_finds_what_slack_shows_raw(self):
        self.assertTrue(
            rich.problems([{"type": "context", "elements": [{"type": "mrkdwn", "text": "*3개*의 잡"}]}])
        )
        self.assertTrue(rich.problems([{"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": [
            {"type": "text", "text": "**3개**의 잡"}]}]}]))  # fmt: skip
        self.assertEqual(
            rich.problems(
                [{"type": "context", "elements": [{"type": "mrkdwn", "text": "*3개* 잡, node_exporter"}]}]
            ),
            [],
        )

    def test_a_value_is_shown_as_it_is(self):
        """A project's purpose with stars and brackets is its text, not a style or a link."""
        shown = rich.read(projects_blocks.home("alice", {"vision": PROJECT}, "/home/shared"))
        self.assertIn(PROJECT["purpose"], shown)


if __name__ == "__main__":
    unittest.main()
