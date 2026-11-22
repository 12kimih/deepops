"""Background work runs behind people at the lowest best-effort I/O priority, never in
the idle class: the idle class gets the disk only when nothing else wants it, so on a
busy disk (a home server's) a scan or a backup in it may never finish (2026-11-23)."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/lib"))
from repo_scan import tracked  # noqa: E402

IDLE = re.compile(r"IOSchedulingClass\s*=\s*(idle|3)\b|ionice\s+(-c\s*3|--class[= ]3|-c3)\b|\"-c\",\s*\"3\"")


class BackgroundPriority(unittest.TestCase):
    def test_nothing_runs_in_the_idle_io_class(self):
        found = [
            f"{path.relative_to(ROOT)}:{n}"
            for path in tracked("roles") + tracked("playbooks")
            if path.is_file()
            for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1)
            if IDLE.search(line)
        ]
        self.assertEqual(found, [], "use IOSchedulingClass=best-effort with IOSchedulingPriority=7")


if __name__ == "__main__":
    unittest.main()
