"""The bot's Home tab (cluster_bot.home_blocks.home): one header per part, in the
glossary's nouns; the rule rows the CLI's usage prints, each led by its state's mark; the
commands as lists in sections, never in a context block (small print is for notes); all
within Slack's limits. The owner found it messy (2026-11-27): one section held every rule
row, and one context block every command."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_bot/files"))
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))  # cluster_tools, which the bot imports
from cluster_bot import commands, home_blocks, projects_blocks  # noqa: E402
from cluster_tools import rich, rules  # noqa: E402

# Slack's limits (Block Kit reference: views, blocks): a Home tab of at most 100 blocks, a
# section's text of 3000 characters and 10 fields, a header's text of 150.
MAX_BLOCKS, MAX_TEXT, MAX_FIELDS, MAX_HEADER = 100, 3000, 10, 150
PROJECT = {"owners": ["alice"], "members": ["bob"], "viewers": [], "purpose": "비전 모델"}


def full():
    rows = [rules.row(r.id, "3개", "10개", state) for r, state in zip(rules.RULES, rules.STATES * 2)]
    view = home_blocks.home("alice", False, {s.feature for s in commands.SUBCOMMANDS if s.feature}, "/cluster", "<#C1>", "<!subteam^S1>", web=[("웹 포털", "https://portal.example/")],
                            cluster=["sq", "qos", "usage"], usage=rules.lines(rows))  # fmt: skip
    view["blocks"] += projects_blocks.home("alice", {f"p{i}": PROJECT for i in range(5)}, "/home/shared")
    return view


def empty():
    view = home_blocks.home("alice", True, set())
    view["blocks"] += projects_blocks.home("alice", {}, "/home/shared")
    return view


def headers(view):
    return [b["text"]["text"] for b in view["blocks"] if b["type"] == "header"]


class Home(unittest.TestCase):
    def test_each_part_has_its_noun_header(self):
        self.assertEqual(
            headers(full()),
            ["alice님의 클러스터", "내 현황", "잡 알림", "명령", "도움 받기", "공동작업 폴더"],
        )
        self.assertEqual(headers(empty()), ["alice님의 클러스터", "잡 알림", "명령", "공동작업 폴더"])

    def test_within_slacks_limits(self):
        for name, view in (("full", full()), ("empty", empty())):
            self.assertLessEqual(len(view["blocks"]), MAX_BLOCKS, name)
            for b in view["blocks"]:
                if b["type"] == "header":
                    self.assertLessEqual(len(b["text"]["text"]), MAX_HEADER, name)
                if b["type"] == "section":
                    self.assertLessEqual(len((b.get("text") or {}).get("text", "")), MAX_TEXT, name)
                    self.assertLessEqual(len(b.get("fields") or []), MAX_FIELDS, name)
            self.assertEqual(rich.problems(view["blocks"], name), [], "nothing shows raw")

    def test_each_rule_row_leads_with_its_state(self):
        shown = rich.read(full()["blocks"])
        for r, state in zip(rules.RULES, rules.STATES * 2):
            self.assertIn(f"{rules.STATE_KO[state]} {r.title}: 3개", shown)
        marks = [e["name"] for b in full()["blocks"] if b["type"] == "rich_text" for s in b["elements"]
                 for e in s.get("elements", []) if e.get("type") == "emoji"]  # fmt: skip
        self.assertEqual(
            marks[: len(rules.RULES)],
            [rules.STATE_MARK[s].strip(":") for s in (rules.STATES * 2)[: len(rules.RULES)]],
        )

    def test_an_empty_figure_is_left_out(self):
        bot = rules.row("login-limits", "", "", "?", "로그인 노드에서만 잴 수 있습니다")
        self.assertEqual(rules.lines([bot]), [":grey_question: 알 수 없음 **로그인 노드 한도**: 로그인 노드에서만 잴 수 있습니다 "
                                             "`usage login-node`"])  # fmt: skip
        self.assertEqual(
            rules.lines([rules.row("disk", "", "5 TB", "ok")]),
            [":large_green_circle: 괜찮음 **디스크 한도**: 한도: 5 TB"],
        )
        self.assertNotIn(": -", "\n".join(rules.lines([rules.row("disk", "", "", "off")])))

    def test_the_commands_are_lists_not_small_print(self):
        view = full()
        notes = [e["text"] for b in view["blocks"] if b["type"] == "context" for e in b["elements"]]
        self.assertFalse(
            [n for n in notes if "/cluster " in n or "`sq`" in n], "no command list in a context"
        )
        shown = rich.read(view["blocks"])
        for title in (commands.BOT_GROUP, commands.LOGIN_GROUP):
            self.assertIn(title, shown)
        lists = [b for b in view["blocks"] if b["type"] == "rich_text"
                 for e in b["elements"] if e["type"] == "rich_text_list"]  # fmt: skip
        self.assertGreaterEqual(len(lists), 2, "each group a list")


if __name__ == "__main__":
    unittest.main()
