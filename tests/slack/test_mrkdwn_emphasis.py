"""Slack mrkdwn bold only closes before a space or punctuation: in "*1시간 전*에" the
closing star is followed by a letter, so Slack shows both stars (the 11-25 notice,
2026-11-25). Korean puts a particle right after a word, so every *bold* in a Slack text
must end before a space or punctuation. This scans every string in the code that writes
Slack messages (an f-string's fields count as letters)."""

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

SOURCES = tracked(
    "roles/cluster_bot/files/cluster_bot/*.py", "roles/cluster_tools_lib/files/cluster_tools/*.py"
)

# An opening star at a word start, its text, and a closing star followed by a letter.
STUCK = re.compile(r"(?<![\w*])\*[^\s*][^*\n]*?(?<=[^\s*])\*(?=[^\W_])")


def strings(path):
    """Every string constant in PATH, with each f-string's fields as "x"."""
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.JoinedStr):
            yield node.lineno, "".join(v.value if isinstance(v, ast.Constant) else "x" for v in node.values)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


class Emphasis(unittest.TestCase):
    def test_the_pattern(self):
        self.assertTrue(STUCK.search("취소 *1시간 전*에 미리"))
        self.assertTrue(STUCK.search("f *x*로"))
        self.assertFalse(STUCK.search("*도움 받기*: 글"))
        self.assertFalse(STUCK.search("QOSGrp*, AssocGrp*, QOSMax*PerUser"))
        self.assertFalse(STUCK.search("glob ** and *args"))
        self.assertTrue(STUCK.search("*a* b *스레드*에"))

    def test_no_slack_text_closes_emphasis_before_a_letter(self):
        bad = [
            f"{p.relative_to(ROOT)}:{line}: {m.group(0)!r}"
            for p in SOURCES
            for line, text in strings(p)
            for m in STUCK.finditer(text)
        ]
        self.assertEqual(bad, [], "Slack shows these marks: end the emphasis before a space or punctuation")


if __name__ == "__main__":
    sys.exit(unittest.main())
