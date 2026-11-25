"""A docs table's "Default hosts" names the hosts its playbook runs on when no hostlist
is given: the hosts: line of the playbook's first play (hostlist's default, or the
literal pattern). The column was typed by hand beside each playbook and went stale
when a playbook's default moved."""

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

HEADER = "| Playbook | Default hosts |"
DEFAULT = re.compile(r"""^\{\{\s*hostlist\s*\|\s*default\(['"]([^'"]+)['"]\)\s*\}\}$""")


def default_hosts(playbook):
    """The first play's hosts with no hostlist given."""
    plays = [p for p in yaml.safe_load(playbook.read_text()) or [] if isinstance(p, dict) and "hosts" in p]
    hosts = str(plays[0]["hosts"]).strip()
    m = DEFAULT.match(hosts)
    return m.group(1) if m else hosts


class PlaybookHosts(unittest.TestCase):
    def test_each_row_names_its_playbooks_default_hosts(self):
        rows, wrong = 0, []
        for doc in tracked("*.md"):
            text = doc.read_text()
            if HEADER not in text:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                m = re.match(r"^\| `([^`]+\.yml)` \| ([^|]+) \|", line)
                if not m:
                    continue
                playbook = ROOT / "playbooks" / m.group(1)
                if not playbook.exists():
                    wrong.append(f"{doc.relative_to(ROOT)}:{number}: no playbooks/{m.group(1)}")
                    continue
                rows += 1
                want, have = default_hosts(playbook), m.group(2).strip()
                if have != want:
                    wrong.append(
                        f"{doc.relative_to(ROOT)}:{number}: {m.group(1)} says {have}, runs on {want}"
                    )
        self.assertGreater(rows, 20, "the tables were not found")
        self.assertEqual(wrong, [])


if __name__ == "__main__":
    unittest.main()
