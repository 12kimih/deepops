"""A test bounds the processes it starts with one shared value, never a number of its
own: a bound guards against a hang, and a hand-picked 5 or 10 seconds fails a correct
test whenever the full check runs on a loaded host (test_vllm at load average 15,
2026-11). Processes take tests/lib/bounds.py CHILD, Ansible runs
scripts/deepops/unrecorded.py TIMEOUT; a test of timeout handling passes its short
value to the code under test, not to subprocess."""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

CALLS = {"run", "Popen", "check_output", "call", "check_call"}


def literal_bounds(source, rel):
    """file:line of each subprocess call in SOURCE whose timeout is a number."""
    found = []
    for node in ast.walk(ast.parse(source, rel)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in CALLS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        ):
            for keyword in node.keywords:
                if keyword.arg == "timeout" and isinstance(keyword.value, ast.Constant):
                    found.append(f"{rel}:{node.lineno}")
    return found


class Bounds(unittest.TestCase):
    def test_no_test_bounds_a_process_with_a_number_of_its_own(self):
        offenders = []
        for path in tracked(
            "tests/*.py", "**/tests/*.py", "**/tests/**/*.py", ":!submodules", ":!roles/galaxy"
        ):
            offenders += literal_bounds((ROOT / path).read_text(), str(path))
        self.assertEqual(sorted(set(offenders)), [], "use timeout=bounds.CHILD (tests/lib/bounds.py)")

    def test_a_number_is_found_and_a_name_is_not(self):
        source = "import subprocess\nsubprocess.run(['x'], timeout=5)\nsubprocess.run(['y'], timeout=bounds.CHILD)\n"
        self.assertEqual(literal_bounds(source, "t.py"), ["t.py:2"])


if __name__ == "__main__":
    unittest.main()
