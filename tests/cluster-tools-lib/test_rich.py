"""cluster_tools.rich: every Slack text is written in one Markdown and sent as rich text
with explicit styles, so bold right before a Korean particle is bold, never stars; and no
module writes Slack's mrkdwn by hand (the guard below)."""

import io
import re
import sys
import tokenize
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
sys.path.insert(0, str(ROOT / "tests/lib"))
from cluster_tools import rich, slack  # noqa: E402
from repo_scan import tracked  # noqa: E402


def section(*elements):
    return {"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": list(elements)}]}


def text(s, **style):
    return {"type": "text", "text": s, **({"style": style} if style else {})}


class Markdown(unittest.TestCase):
    def test_bold_before_a_particle_is_a_style(self):
        self.assertEqual(rich.blocks("**3개**의 잡"), [section(text("3개", bold=True), text("의 잡"))])

    def test_spans_references_and_emoji(self):
        (block,) = rich.blocks(
            "`scancel 1`로 <@U1> <!subteam^S2> <!here> <#C3> [문서](https://d/) :zzz: _참고_"
        )
        kinds = [e["type"] for e in block["elements"][0]["elements"]]
        self.assertEqual(kinds, ["text", "text", "user", "text", "usergroup", "text", "broadcast", "text",
                                 "channel", "text", "link", "text", "emoji", "text", "text"])  # fmt: skip
        self.assertEqual(block["elements"][0]["elements"][0], text("scancel 1", code=True))
        self.assertEqual(block["elements"][0]["elements"][-1], text("참고", italic=True))

    def test_lists_fences_and_quotes(self):
        (block,) = rich.blocks("앞\n- 하나\n  - 둘\n```\n사용자  GPU\n```\n> 인용")
        self.assertEqual([e["type"] for e in block["elements"]],
                         ["rich_text_section", "rich_text_list", "rich_text_list", "rich_text_preformatted",
                          "rich_text_quote"])  # fmt: skip
        self.assertEqual([e.get("indent") for e in block["elements"][1:3]], [0, 1])
        self.assertEqual(block["elements"][3]["elements"], [text("사용자  GPU")])

    def test_a_value_is_shown_as_it_is(self):
        """slack.escape: a name with stars, brackets, a mention or an emoji is its text."""
        value = "a_b **c** [d](e) <!channel> :zzz: `f`"
        shown = rich.read(rich.blocks(f"**{slack.escape(value)}** 님, `{slack.escape(value)}`"))
        self.assertEqual(shown, f"{value} 님, `{value}`")
        self.assertIn(
            "&lt;!channel&gt;", rich.message(slack.escape(value))["text"], "the fallback mentions no one"
        )

    def test_a_table_keeps_its_columns(self):
        rows = [("a_b", "1"), ("*c*", "22")]
        (block,) = rich.blocks(slack.table(("사용자", "GPU"), rows))
        self.assertEqual(block["elements"][0]["elements"][0]["text"], "사용자  GPU\na_b     1\n*c*     22")

    def test_the_fallback_has_no_markers(self):
        msg = rich.message("**3개**의 잡, `scancel 1` [문서](https://d/)")
        self.assertEqual(msg["text"], "3개의 잡, scancel 1 문서 (https://d/)")

    def test_mrkdwn_keeps_a_style_only_where_slack_shows_it(self):
        self.assertEqual(rich.mrkdwn("**할당된 GPU**\n3장"), "*할당된 GPU*\n3장")
        self.assertEqual(rich.mrkdwn("**3개**의 잡"), "3개의 잡", "no stars Slack would show as typed")
        self.assertEqual(rich.mrkdwn("`quota -s` 로"), "`quota -s` 로")

    def test_what_mrkdwn_cannot_show_is_rich_or_plain_text(self):
        self.assertEqual(rich.context("`quota -s`로 봅니다")["type"], "rich_text", "the command stays code")
        self.assertEqual(rich.context("`quota -s` 명령")["type"], "context")
        beside = rich.section(f"목적: {slack.escape('**비전**')}", accessory={"type": "button"})
        self.assertEqual(beside["text"], {"type": "plain_text", "text": "목적: **비전**", "emoji": True})

    def test_a_long_text_is_split_and_nothing_is_lost(self):
        rows = [f"row {i:04d} " + "x" * 40 for i in range(200)]
        parts = rich.blocks("head\n```\n" + "\n".join(rows) + "\n```")
        self.assertGreater(len(parts), 1)
        shown = [ln for p in parts for ln in rich.read([p]).splitlines() if ln.startswith("row ")]
        self.assertEqual(shown, rows)


# Slack's mrkdwn bold (*X*) in a string literal: the Markdown here writes **X**.
STAR = re.compile(r"(?<![*\w\\])\*(?=[^\s*])[^*\n]*?[^\s*\\]\*(?![*\w])")
GLOB = re.compile(r"""(["'])\*\w+\*\1""")
SENDERS = ("roles/cluster_bot/files/cluster_bot/*.py", "roles/cluster_tools_lib/files/cluster_tools/*.py")


def literals(path):
    """(line, text) of PATH's string literals and f-string parts that could be a message:
    docstrings, raw strings (patterns) and a glob of one word ("*Invalid*") left out."""
    tokens = tokenize.generate_tokens(io.StringIO(path.read_text()).readline)
    for tok in tokens:
        prefix = tok.string[: len(tok.string) - len(tok.string.lstrip("rbufRBUF"))].lower()
        if tok.type == tokenize.STRING and "r" not in prefix and not GLOB.fullmatch(tok.string):
            if not tok.string[len(prefix) :].startswith(('"""', "'''")):
                yield tok.start[0], tok.string
        elif tok.type == getattr(tokenize, "FSTRING_MIDDLE", None):
            yield tok.start[0], tok.string


class NoHandWrittenMrkdwn(unittest.TestCase):
    """Each module once wrote mrkdwn itself, and Slack showed "*N개*의" as typed."""

    def test_only_rich_writes_mrkdwn(self):
        found = []
        for path in tracked(*SENDERS):
            if path.name == "rich.py":
                continue
            rel = path.relative_to(ROOT)
            found += [f"{rel}:{n}: mrkdwn" for n, s in literals(path) if "mrkdwn" in s]
            found += [f"{rel}:{n}: {s}" for n, s in literals(path) if STAR.search(s)]
        self.assertEqual(found, [], "write the text in cluster_tools.rich's Markdown (**bold**)")
        self.assertGreater(len(tracked(*SENDERS)), 20)

    def test_the_bot_sends_every_text_through_rich(self):
        app = (ROOT / "roles/cluster_bot/files/cluster_bot/app.py").read_text()
        calls = re.findall(r"client\.chat_\w+\((?:[^()]|\([^()]*\))*\)", app, re.S)
        self.assertTrue(calls)
        self.assertEqual([c for c in calls if re.search(r"\btext=", c)], [], "**rich.message(text, blocks)")


if __name__ == "__main__":
    unittest.main()
