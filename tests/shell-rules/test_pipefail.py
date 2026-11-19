"""Under `set -o pipefail` a pipeline fails when any stage does. A reader that stops
early (grep -q, grep -m) lets the writer before it die of SIGPIPE when its output is
long, and whether it is still writing depends on timing: the Run:ai test's
`echo "$out" | grep -q ...` failed only under the full check's parallel load. Feed the
text without a pipe (grep -q PATTERN <<<"$out") instead."""

import re
import sys
import unittest

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

PIPEFAIL = re.compile(
    r"^\s*set\s+-[a-z]*o\s+pipefail|^\s*set\s+-o\s+pipefail|set -euo pipefail|set -uo pipefail", re.M
)
EARLY_READER = re.compile(r"\|\s*grep\s+-[A-Za-z]*[qm]")


def shell_files():
    for path in tracked("*.sh", "*.bash", "scripts/*", "tests/*", "roles/*/files/*", "roles/*/templates/*"):
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        first = text.split("\n", 1)[0]
        if path.suffix in (".sh", ".bash") or ("sh" in first and first.startswith("#!")):
            yield path, text


class Pipefail(unittest.TestCase):
    def test_no_early_reader_in_a_pipefail_script(self):
        offenders, scanned = [], 0
        for path, text in shell_files():
            if not PIPEFAIL.search(text):
                continue
            scanned += 1
            for number, line in enumerate(text.splitlines(), 1):
                if EARLY_READER.search(line) and not line.lstrip().startswith("#"):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
        self.assertGreater(scanned, 5, "the scan found the repository's scripts")
        self.assertEqual(offenders, [], 'feed the text with <<<"$var", not a pipe into grep -q/-m')


if __name__ == "__main__":
    unittest.main()
