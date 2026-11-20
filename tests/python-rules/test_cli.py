"""Every script under scripts/ reads its command line with argparse: --help answers
without doing the work, and an option it does not know is refused (exit 2).

Hand-parsed argv ignored what it did not expect: `upstream.py --help` ran the whole
upstream check for minutes, and a mistyped `--chek` ran a script's default action, which
for several rewrites files. The repository's own tools build their parser one way,
scripts/deepops/cli.py, so each prints its docstring as its help. The validators
(scripts/validation) run alone on cluster nodes and keep argparse of their own.
"""

import ast
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

STANDALONE = ("scripts/validation/", "scripts/maas_inventory.py")


def is_main(node):
    """`if __name__ == "__main__":`"""
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in node.test.comparators)
    )


def scripts():
    """The tracked scripts/ programs (a module with a __main__ block), tests left out."""
    for path in tracked("scripts/*.py", ":!**/tests/**"):
        if any(is_main(node) for node in ast.parse(path.read_text(), str(path)).body):
            yield path


def run(path, *args):
    return subprocess.run(
        [sys.executable, str(path), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
    )


class Cli(unittest.TestCase):
    def test_help_answers_and_unknown_options_are_refused(self):
        found = list(scripts())
        self.assertGreater(len(found), 20)
        for path in found:
            rel = str(path.relative_to(ROOT))
            with self.subTest(script=rel):
                shown = run(path, "--help")
                self.assertEqual(shown.returncode, 0, shown.stderr)
                self.assertTrue(shown.stdout.startswith("usage:"), shown.stdout[:200])
                refused = run(path, "--no-such-option")
                self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)

    def test_the_repository_tools_build_their_parser_one_way(self):
        for path in scripts():
            rel = str(path.relative_to(ROOT))
            if rel.startswith(STANDALONE):
                continue
            with self.subTest(script=rel):
                self.assertIn("cli.parser(__doc__)", path.read_text())


if __name__ == "__main__":
    unittest.main()
