"""scripts/deepops/check-template-syntax.py refuses what breaks a template at deploy time:
Jinja syntax that the file's own language hides (bash's array length opens a Jinja
comment), and a string literal escape Python deprecates and will refuse."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_template_syntax", ROOT / "scripts/deepops/check-template-syntax.py"
)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


class TemplateSyntax(unittest.TestCase):
    def test_a_valid_template_passes(self):
        self.assertIsNone(checker.problem("x={{ y | default('a\\\\s') }}\n{% if z %}1{% endif %}\n"))

    def test_bash_array_length_is_refused(self):
        self.assertIn("comment", checker.problem('n=${#items[@]}\necho "$n"\n'))

    def test_an_unknown_escape_is_refused(self):
        self.assertIn("invalid escape", checker.problem("{{ x is match('a\\sb') }}\n"))


if __name__ == "__main__":
    unittest.main()
