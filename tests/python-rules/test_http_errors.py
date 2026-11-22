"""An HTTP error an urlopen raises is closed where it is caught.

urllib's HTTPError holds the response open; caught and dropped, it is closed only by
the garbage collector, a ResourceWarning that check.sh's strict runner fails on (and,
in a long-running tool, a socket held for nothing). It is a URLError and an OSError, so
a handler for either catches it too: such a handler must come after one of its own,
which closes it (`with e:` or `e.close()`). Tests count too: a smoke test that waits
for a server caught the 503 of one still starting and leaked it.
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

BROAD = ("URLError", "OSError", "ConnectionError", "IOError", "Exception", "BaseException")


def _names(handler):
    return ast.unparse(handler.type) if handler.type else "BaseException"


def _closes(handler):
    """Whether the handler closes what it caught: `with e:` or `e.close()`."""
    name = handler.name
    if not name:
        return False
    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if isinstance(node, ast.With) and any(
            isinstance(item.context_expr, ast.Name) and item.context_expr.id == name for item in node.items
        ):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "close"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == name
        ):
            return True
    return False


def offenders(source, rel):
    found = []
    for node in ast.walk(ast.parse(source, rel)):
        if not isinstance(node, ast.Try):
            continue
        if "urlopen" not in ast.unparse(ast.Module(body=node.body, type_ignores=[])):
            continue
        closed = False
        for handler in node.handlers:
            names = _names(handler)
            if "HTTPError" in names and "URLError" not in names:
                if not _closes(handler):
                    found.append(f"{rel}:{handler.lineno}: an HTTPError handler that does not close it")
                closed = True
            elif any(b in names for b in BROAD) and not closed:
                found.append(f"{rel}:{handler.lineno}: {names} catches an HTTPError it never closes")
    return found


class HttpErrors(unittest.TestCase):
    def test_every_caught_http_error_is_closed(self):
        found, scanned = [], 0
        for path in tracked("*.py", ":!submodules", ":!roles/galaxy"):
            scanned += 1
            found += offenders(path.read_text(), path.relative_to(ROOT).as_posix())
        self.assertGreater(scanned, 100)
        self.assertEqual(found, [], "close it: except urllib.error.HTTPError as e: with e: ...")

    def test_the_rule_tells_the_cases_apart(self):
        leak = "try:\n    urlopen(r)\nexcept (urllib.error.URLError, OSError):\n    pass\n"
        closed = (
            "try:\n    urlopen(r)\nexcept urllib.error.HTTPError as e:\n    with e:\n        pass\n"
            "except urllib.error.URLError:\n    pass\n"
        )
        unclosed = "try:\n    urlopen(r)\nexcept urllib.error.HTTPError as e:\n    raise X from e\n"
        self.assertEqual(len(offenders(leak, "a.py")), 1)
        self.assertEqual(offenders(closed, "b.py"), [])
        self.assertEqual(len(offenders(unclosed, "c.py")), 1)


if __name__ == "__main__":
    unittest.main()
