"""commit_message.py: the commit message standard."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "commit_message", ROOT / "scripts" / "deepops" / "commit_message.py"
)
cm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cm)

BODY = "The reason, in lines of at most seventy-two characters, wrapped by hand.\n"


class Standard(unittest.TestCase):
    def test_a_message_to_the_standard_passes(self):
        for message in (
            "fix(config_lint): dead finds a site's idle settings\n\n" + BODY,
            "feat: one line only\n",
            "docs(a,b): two scopes\n\n    an indented line may be as long as it needs to be, as code is\n",
            "chore(versions): bump\n\nhttps://example.org/a/very/long/link/that/cannot/be/broken/at/all/really\n",
            "Merge branch 'upstream' into the fork, with a subject git wrote for it as it does\n",
            "fix: x\n\n# a comment line is not part of the message, however long it is, it never counts\n",
        ):
            self.assertEqual(cm.problems(message), [], message)

    def test_each_rule(self):
        cases = {
            "Fix the thing\n": "not '<type>(<scope>): <summary>'",
            "update(x): a type outside the list\n": "not '<type>(<scope>): <summary>'",
            "fix(Upper Case): scope\n": "not '<type>(<scope>): <summary>'",
            "fix: " + "x" * 68 + "\n": "73 characters",
            "fix: ends with a period.\n": "ends with a period",
            "fix: wip probe\n": "work in progress",
            "fixup! fix: the earlier one\n": "not '<type>",
            "fix: x\nthe body right after the subject\n": "line 2: not blank",
            "fix: x\n\n" + "word " * 15 + "\n": "line 3: 75 characters",
            "fix: x \n": "trailing space",
            "fix: an arrow →\n": "non-ASCII",
            "fix: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n": "tool-attribution trailer",
            "\n\n": "empty subject",
        }
        for message, expected in cases.items():
            found = cm.problems(message)
            self.assertTrue(any(expected in f for f in found), (message, found))

    def test_wiped_is_no_wip(self):
        self.assertEqual(cm.problems("fix(slurm): survive a wiped plugin\n"), [])

    def test_the_scissors_end_the_message(self):
        message = (
            "fix: x\n\n"
            + BODY
            + "# ------------------------ >8 ------------------------\n"
            + "y" * 200
            + "\n"
        )
        self.assertEqual(cm.problems(message), [])


if __name__ == "__main__":
    unittest.main()
