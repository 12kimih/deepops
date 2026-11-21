"""No code puts plugins/filter on sys.path.

The directory holds Ansible filter files named like standard library modules
(secrets.py) and like other packages' modules (accounts.py): on sys.path they shadow
them for everything the process imports afterwards, so `secrets.token_hex` vanished in a
test that also loaded the roster filter (2026-11-22). A filter file is loaded on its own
with tests/lib/in_tree.load("plugins/filter/<name>.py"), or by Ansible as a plugin.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

ON_PATH = re.compile(r"sys\.path\.(insert|append)\([^)]*plugins[\"'/ ,]+[^)]*filter")


class FilterPath(unittest.TestCase):
    def test_no_code_puts_the_filter_directory_on_sys_path(self):
        found = [
            f"{path.relative_to(ROOT)}:{n}"
            for path in tracked("**/*.py")
            if path != Path(__file__).resolve()
            for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1)
            if ON_PATH.search(line)
        ]
        self.assertEqual(found, [], 'load the file with in_tree.load("plugins/filter/<name>.py")')


if __name__ == "__main__":
    unittest.main()
