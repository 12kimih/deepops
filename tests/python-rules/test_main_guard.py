"""A test file's `if __name__ == "__main__":` is its last statement. The check imports
each suite (python -m unittest) and runs every class in it, but a file run on its own,
`python tests/x/test_y.py`, stops at unittest.main(): a class written below it never
runs there and its file still says OK (a new class appended to tests/nhc/test_conf.py
passed that way against the old code, 2026-11-23)."""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402


def is_main_guard(node):
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in node.test.comparators)
    )


def code_after_guard(source, rel):
    """file:line of the main guard when a statement follows it, else None."""
    body = ast.parse(source, rel).body
    guards = [i for i, node in enumerate(body) if is_main_guard(node)]
    if guards and guards[0] != len(body) - 1:
        return f"{rel}:{body[guards[0]].lineno}"
    return None


class MainGuard(unittest.TestCase):
    def test_every_test_file_ends_with_its_main_guard(self):
        offenders = [
            found
            for path in tracked(
                "tests/*.py", "**/tests/*.py", "**/tests/**/*.py", ":!submodules", ":!roles/galaxy"
            )
            if (
                found := code_after_guard(
                    (ROOT / path).read_text(),
                    str(Path(path).relative_to(ROOT) if Path(path).is_absolute() else path),
                )
            )
        ]
        self.assertEqual(offenders, [], "move the main guard to the end of the file")

    def test_the_rule_tells_the_cases_apart(self):
        guard = 'if __name__ == "__main__":\n    unittest.main()\n'
        self.assertIsNone(code_after_guard("class A: pass\n" + guard, "ok.py"))
        self.assertIsNone(code_after_guard("class A: pass\n", "none.py"))
        self.assertEqual(code_after_guard(guard + "class B: pass\n", "bad.py"), "bad.py:1")


if __name__ == "__main__":
    unittest.main()
