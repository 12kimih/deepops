"""Every process output the shipped Python decodes says how to treat bytes that are not
UTF-8: subprocess decodes text strictly by default, so one such byte (a file name, a job
comment, a commit's content, a tool's localized message) raised UnicodeDecodeError and
stopped a validator, a git hook or a removal module with a traceback instead of an answer.

  errors="replace"           text that is shown or parsed
  errors="surrogateescape"   paths that go back to the OS or to git, as os.fsdecode
                             keeps them (roles/stale_files: FS): a replaced character
                             makes a path differ from itself

cluster_tools' proc.run applies errors="replace" itself. Tests are left out: they run
the tools on fixtures of their own, where a strict decode fails loudly in the test.
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

CALLS = {"run", "Popen", "check_output", "call", "check_call"}
TEXT = {"text", "universal_newlines", "encoding"}


def shipped():
    for path in tracked("*.py", ":!submodules", ":!roles/galaxy", ":!tests", ":!**/tests/**"):
        yield path


def offenders(source, rel):
    found = []
    for node in ast.walk(ast.parse(source, rel)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in CALLS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        ):
            continue
        names = {k.arg for k in node.keywords}
        spread = any(k.arg is None for k in node.keywords)  # **FS names both
        decodes = any(
            k.arg in TEXT and not (isinstance(k.value, ast.Constant) and k.value.value in (False, None))
            for k in node.keywords
        )
        if decodes and "errors" not in names and not spread:
            found.append(f"{rel}:{node.lineno}")
    return found


class SubprocessText(unittest.TestCase):
    def test_every_decoded_output_says_how_to_treat_undecodable_bytes(self):
        found, scanned = [], 0
        for path in shipped():
            scanned += 1
            found += offenders(path.read_text(), path.relative_to(ROOT).as_posix())
        self.assertGreater(scanned, 50, "the scan found almost no Python")
        self.assertEqual(found, [], 'pass errors="replace", or "surrogateescape" for paths')

    def test_the_rule_tells_a_strict_decode(self):
        src = (
            "import subprocess\n"
            "subprocess.run(['x'], text=True)\n"
            "subprocess.run(['x'], text=True, errors='replace')\n"
            "subprocess.run(['x'], **FS)\n"
            "subprocess.run(['x'], capture_output=True)\n"
            "subprocess.Popen(['x'], universal_newlines=True)\n"
        )
        self.assertEqual(offenders(src, "t.py"), ["t.py:2", "t.py:6"])


if __name__ == "__main__":
    unittest.main()
