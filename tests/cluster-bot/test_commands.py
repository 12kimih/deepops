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
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))  # cluster_tools, which the bot imports
from cluster_bot import commands, home_blocks  # noqa: E402

APP = ROOT / "roles/cluster_bot/files/cluster_bot/app.py"

# Slack's built-in slash commands, as its Help Center lists them ("Use shortcuts to take
# actions in Slack", table "Built-in shortcuts": https://slack.com/help/articles/201259356
# (now also /help/articles/360057554553), read 2026-11-24): an app's command of the same
# name clashes with Slack's own. /kick is /remove's other name; /msg has two forms.
SLACK_BUILTINS = {
    "/archive", "/browse", "/collapse", "/create", "/darkmode", "/description", "/dm", "/dnd",
    "/downloads", "/drafts", "/expand", "/feedback", "/gif", "/huddle", "/invite", "/kick",
    "/leave", "/mentions", "/msg", "/mute", "/notifications", "/people", "/remind", "/remove",
    "/rename", "/saved", "/search", "/send", "/shortcuts", "/shrug", "/snippet", "/status",
    "/topic", "/workflow",
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
FAMILIES = "chat|conversations|users|usergroups|pins|views|reactions|files|bookmarks|canvases|team|auth|apps"


def methods_called():
    found = set()
    for path in CODE:
        text = path.read_text()
        found |= set(re.findall(rf"[\"']((?:{FAMILIES})(?:\.[a-zA-Z]+)+)[\"']", text))
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
        help_text = commands.help_lines("/cluster", {s.feature for s in commands.SUBCOMMANDS if s.feature})
        docs = commands.doc_lines("/cluster")
        for name in commands.NAMES:
            self.assertIn(f"`/cluster {name}`", help_text)
            self.assertTrue(any(f"`/cluster {name}`" in line for line in docs), name)
        self.assertEqual(len(re.findall(r"@app\.command\(", APP.read_text())), 1, "one slash command")

    def test_no_subcommand_is_written_as_a_slash_command_of_its_own(self):
        """The bot once had a slash command per request; a subcommand is `/cluster join`
        now, and docs that kept the old names sent people to Slack's own leave command."""
        old = re.compile(r"(?:^|[\s(])`?/(" + "|".join(commands.NAMES) + r")\b(?![/\w])(?!`? is Slack's own)")
        files = tracked(*(f"roles/cluster_bot/*{s}" for s in (".py", ".yml", ".j2", ".md")),
                        "docs/slurm-cluster/cluster-bot.md", "tests/cluster-bot/*.py")  # fmt: skip
        found = []
        for path in files:
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if old.search(line) and "SLACK_BUILTINS" not in line and '"/leave"' not in line:
                    found.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
        self.assertEqual(found, [], "write the subcommand: /cluster join, or 'the join request'")

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

    def test_help_and_home_list_the_cluster_commands_with_their_own_summaries(self):
        # 2026-11-27: the help named only /cluster subcommands, never sq or disk-usage
        import importlib

        names = ["sq", "disk-usage"]
        text = commands.help_lines("/cluster", set(), footer="foot", cluster=names)
        for name in names:
            summary = importlib.import_module(f"cluster_tools.{name.replace('-', '_')}").COMMAND.summary_ko
            self.assertTrue(summary, f"{name} has a Korean summary")
            self.assertIn(f"`{name}`: {summary}", text)
        self.assertTrue(text.endswith("foot"))
        self.assertNotIn("로그인 노드의 명령", commands.help_lines("/cluster", set()), "none: no heading")
        home = home_blocks.home("alice", False, set(), cluster=names)
        self.assertIn("'text': 'disk-usage', 'style': {'code': True}", str(home))

    def test_every_published_command_has_a_korean_summary(self):
        import importlib

        import yaml

        builtin = yaml.safe_load((ROOT / "roles/cluster_tools_defaults/defaults/main.yml").read_text())
        for name in builtin["cluster_tools_builtin"]:
            command = importlib.import_module(f"cluster_tools.{name.replace('-', '_')}").COMMAND
            self.assertTrue(command.summary_ko, name)

    def test_the_home_tab_opens_each_service_the_config_gives_an_address(self):
        web = home_blocks.links(
            {"portal_url": "https://portal.example/", "dashboard_url": "", "docs_url": "https://d/"}
        )
        self.assertEqual(web, [("웹 포털", "https://portal.example/"), ("사용 안내", "https://d/")])
        home = home_blocks.home("alice", False, set(), web=web)
        buttons = next(b for b in home["blocks"] if b["type"] == "actions")["elements"]
        self.assertEqual([(b["text"]["text"], b["url"]) for b in buttons], web)
        self.assertNotIn("actions", str(home_blocks.home("alice", False, set())), "no address, no buttons")

    def test_no_command_is_one_of_slacks_own(self):
        declared = [c["command"] for c in commands.manifest("/cluster")["features"]["slash_commands"]]
        self.assertEqual([c for c in declared if c in SLACK_BUILTINS], [])
        self.assertIn("/leave", SLACK_BUILTINS, "the clash the one command avoids")

    def test_the_manifest_has_every_scope_the_code_needs_and_what_the_site_keeps(self):
        called = methods_called()
        self.assertIn("chat.postMessage", called, "the scan finds calls")
        unknown = sorted(called - set(commands.API_SCOPES))
        self.assertEqual(unknown, [], "map each method the code calls to its scope in API_SCOPES")
        needed = {
            scope
            for m, s in commands.API_SCOPES.items()
            if m in called and s
            for scope in ((s,) if isinstance(s, str) else s)
        }
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
