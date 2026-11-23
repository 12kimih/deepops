"""cluster_bot.commands: one slash command, its subcommands one table that the
dispatcher, the help, the Home tab, the manifest and the docs all read; the manifest's
scopes cover every Slack method the repository calls; no name Slack uses itself."""

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/lib"))
from repo_scan import tracked  # noqa: E402

sys.path.insert(0, str(ROOT / "roles/cluster_bot/files"))
from cluster_bot import commands, home_blocks  # noqa: E402

APP = ROOT / "roles/cluster_bot/files/cluster_bot/app.py"

# Slack's built-in slash commands (https://slack.com/help/articles/201259356-Slack-commands
# and its "Use built-in slash commands" list, 2026-11): an app's command of the same
# name clashes with Slack's own.
SLACK_BUILTINS = {
    "/active", "/away", "/apps", "/archive", "/call", "/collapse", "/dm", "/dnd", "/expand",
    "/feed", "/feedback", "/invite", "/join", "/kick", "/leave", "/me", "/msg", "/mute",
    "/open", "/prefs", "/remind", "/remove", "/rename", "/search", "/shortcuts", "/shrug",
    "/star", "/status", "/topic", "/who",
}  # fmt: skip

# The Slack Web API methods the repository's code calls, as method names: Bolt's client
# (client.chat_postMessage) and cluster_tools.slack's (_api("chat.postMessage")).
CODE = [
    *tracked("roles/cluster_bot/files/cluster_bot"),
    *tracked("roles/cluster_tools_lib/files/cluster_tools"),
]
CODE = [p for p in CODE if p.suffix == ".py"]
# The deploys' own calls (ansible.builtin.uri to slack.com/api/<method>).
TASKS = [p for p in tracked("roles") if p.parent.name == "tasks" and p.suffix == ".yml"]
FAMILIES = "chat|conversations|users|usergroups|pins|views|reactions|files|bookmarks|team|auth|apps"


def methods_called():
    found = set()
    for path in CODE:
        text = path.read_text()
        found |= set(re.findall(rf"[\"']((?:{FAMILIES})\.[a-zA-Z]+)[\"']", text))
        for family, name in re.findall(rf"\bclient\.({FAMILIES})_([a-zA-Z]+)\(", text):
            found.add(f"{family}.{name}")
    for path in TASKS:
        found |= set(re.findall(rf"slack\.com/api/((?:{FAMILIES})\.[a-zA-Z]+)", path.read_text()))
    return found


class Commands(unittest.TestCase):
    def test_every_subcommand_has_a_handler_a_help_line_and_a_doc_line(self):
        tree = ast.parse(APP.read_text())
        handled = next(
            {k.value for k in node.value.keys}
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "handlers"
        )
        self.assertEqual(handled, set(commands.NAMES))
        help_text = commands.help_lines("/cluster", {"join", "unban", "project"})
        docs = commands.doc_lines("/cluster")
        for name in commands.NAMES:
            self.assertIn(f"`/cluster {name}`", help_text)
            self.assertTrue(any(f"`/cluster {name}`" in line for line in docs), name)
        self.assertEqual(len(re.findall(r"@app\.command\(", APP.read_text())), 1, "one slash command")

    def test_parse(self):
        self.assertEqual(commands.parse(""), ("help", ""))
        self.assertEqual(commands.parse("  Join "), ("join", ""))
        self.assertEqual(commands.parse("unban 203.0.113.7"), ("unban", "203.0.113.7"))
        self.assertEqual(commands.parse("frobnicate"), ("help", ""), "an unknown word shows the help")

    def test_help_offers_what_the_cluster_runs_and_ends_with_the_footer(self):
        text = commands.help_lines("/cluster", {"project"}, footer="Ask in the thread")
        self.assertNotIn("/cluster join", text)
        self.assertIn("/cluster project", text)
        self.assertTrue(text.endswith("Ask in the thread"))
        home = home_blocks.home("alice", False, {"project"}, "/cl", "foot")
        self.assertIn("`/cl project`", str(home), "the Home tab's list is the same help")

    def test_no_command_is_one_of_slacks_own(self):
        declared = [c["command"] for c in commands.manifest("/cluster")["features"]["slash_commands"]]
        self.assertEqual([c for c in declared if c in SLACK_BUILTINS], [])
        self.assertIn("/leave", SLACK_BUILTINS, "the clash the one command avoids")

    def test_the_manifest_has_every_scope_the_code_needs_and_what_the_site_keeps(self):
        called = methods_called()
        self.assertIn("chat.postMessage", called, "the scan finds calls")
        unknown = sorted(called - set(commands.API_SCOPES))
        self.assertEqual(unknown, [], "map each method the code calls to its scope in API_SCOPES")
        needed = {s for m, s in commands.API_SCOPES.items() if m in called and s}
        m = commands.manifest("/cluster", extra_scopes=["incoming-webhook"])
        scopes = set(m["oauth_config"]["scopes"]["bot"])
        self.assertLessEqual(needed, scopes)
        self.assertIn("incoming-webhook", scopes, "kept for the webhook another tool posts through")
        # Every scope SCOPES lists is used: by a method the code calls, or by what no
        # method shows (the command itself, posting where not invited, the pair users:read
        # goes with).
        self.assertEqual(set(commands.SCOPES) - {"commands", "chat:write.public", "users:read"}, needed)
        self.assertEqual(
            (m["display_information"]["name"], m["features"]["bot_user"]["display_name"]),
            ("cluster-bot", "cluster-bot"),
        )
        self.assertEqual([c["command"] for c in m["features"]["slash_commands"]], ["/cluster"])
        self.assertTrue(m["settings"]["socket_mode_enabled"])
        self.assertEqual(m["settings"]["event_subscriptions"]["bot_events"], ["app_home_opened"])
        self.assertLessEqual(len(m["features"]["slash_commands"][0]["description"]), 100)


if __name__ == "__main__":
    unittest.main()
