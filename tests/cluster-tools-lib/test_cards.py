"""cluster_tools.cards: one look for every message the cluster posts (owner, 2026-11-27):
a header with the message's noun and time, parts under short bold labels with the CLI's
state marks, aligned tables, small print only for notes, and the step last with the rule's
own command. The reports and the rule DMs are built from it, never laid out by hand."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "roles/cluster_tools_lib/files"
sys.path.insert(0, str(LIB))
from cluster_tools import cards, rich, rules  # noqa: E402

REPORTS = ("disk_report.py", "usage_report.py", "io_report.py")


class Cards(unittest.TestCase):
    def test_a_header_is_a_noun_and_its_time(self):
        self.assertEqual(
            cards.title("디스크 사용량", "2026-11-28")["text"]["text"], "디스크 사용량  2026-11-28"
        )

    def test_a_part_leads_with_its_state_mark_and_bold_label(self):
        (block,) = cards.part("디스크", "본문", "warn")
        first = block["elements"][0]["elements"]
        self.assertEqual(first[0], {"type": "emoji", "name": "large_yellow_circle"})
        self.assertIn({"type": "text", "text": "디스크", "style": {"bold": True}}, first)

    def test_facts_stay_within_a_section(self):
        self.assertEqual(len(cards.facts([("a", "1", "ok")] * 10)["fields"]), 10)
        with self.assertRaises(ValueError):
            cards.facts([("a", "1", "")] * 11)

    def test_a_rule_dm_has_the_rules_title_and_its_step_last(self):
        text = f"본문입니다.\n{rules.check_note('disk')}"
        fallback, blocks = cards.notice("disk", text)
        self.assertEqual(fallback, text)
        self.assertEqual(blocks[0]["text"]["text"], rules.BY_ID["disk"].title)
        self.assertTrue(rich.read(blocks[-1:]).endswith(rules.check_note("disk")))
        with self.assertRaises(ValueError):
            cards.notice("disk", "a step must be its own line")

    def test_the_reports_lay_out_nothing_by_hand(self):
        """A header or a hand-written status emoji in a report is a second design."""
        lib = LIB / "cluster_tools"
        found = []
        for name in REPORTS:
            text = (lib / name).read_text()
            for pattern in (r'"type": "header"', r":(?:warning|no_entry|zzz|large_orange_circle):"):
                found += [f"{name}: {m.group(0)}" for m in re.finditer(pattern, text)]
        self.assertEqual(found, [], "build messages with cluster_tools.cards")


if __name__ == "__main__":
    unittest.main()
