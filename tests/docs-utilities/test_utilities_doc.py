"""docs/deepops/utilities.md lists every utility playbook with the hosts it targets.

The "Default hosts" column restates each playbook's first play: written by hand, it went
wrong as plays were retargeted (and new utilities went unlisted). The playbook is the
source; this keeps the table equal to it.
"""

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

DOC = ROOT / "docs" / "deepops" / "utilities.md"
ROW = re.compile(r"^\| `(?P<path>[\w./-]+\.yml)` \| (?P<hosts>[^|]+?) \|", re.M)
HOSTLIST = re.compile(r"^\{\{ hostlist \| default\('(?P<default>[^']+)'\) \}\}$")


def default_hosts(playbook: Path) -> str:
    """The first play's hosts, or the default of its `-e hostlist=` pattern."""
    plays = yaml.safe_load(playbook.read_text(encoding="utf-8"))
    hosts = next(p["hosts"] for p in plays if "hosts" in p)
    found = HOSTLIST.match(hosts)
    return found["default"] if found else hosts


class UtilitiesDoc(unittest.TestCase):
    rows = {m["path"]: m["hosts"] for m in ROW.finditer(DOC.read_text(encoding="utf-8"))}

    def test_every_utility_playbook_is_listed(self):
        listed = set(self.rows)
        playbooks = tracked("playbooks/utilities/*.yml")
        self.assertTrue(playbooks)
        for playbook in playbooks:
            with self.subTest(playbook=playbook.name):
                self.assertIn(f"utilities/{playbook.name}", listed)

    def test_the_hosts_column_is_the_playbooks_own(self):
        self.assertTrue(self.rows)
        for path, hosts in self.rows.items():
            with self.subTest(path=path):
                self.assertEqual(hosts, default_hosts(ROOT / "playbooks" / path))


if __name__ == "__main__":
    unittest.main()
