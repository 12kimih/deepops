"""Every version deepops pins names where its releases are published.

scripts/deepops/upstream.py reads the `# upstream:` line above each pin to report the
pins behind their upstream; a pin without one would silently drop out of that report
and age. Container images (repo:tag@sha256) and pyproject.toml's pins need no line.
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# A version pin: a *_version or *_branch setting whose value is a literal version
# (minimums such as runai_gpu_operator_min_version are requirements, not pins).
PIN = re.compile(r'^([a-z_][a-z0-9_]*(?:_version|_branch)):\s*"?(v?\d+(?:\.\d+)*(?:-[\w.~]+)?)"?\s*$')
SOURCE = re.compile(r"^\s*#\s*upstream:\s*(git|pypi|helm|deb|oci|cuda|manual|hold)\b")


class UpstreamSources(unittest.TestCase):
    def test_every_pin_names_its_upstream(self):
        files = subprocess.run(
            ["git", "ls-files", "roles/*/defaults/main.yml", "roles/*/vars/*.yml", "roles/*/vars/*.yaml"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.split()  # fmt: skip
        missing, pins = [], 0
        for rel in files:
            if rel.startswith("roles/galaxy/"):
                continue
            lines = (ROOT / rel).read_text().splitlines()
            for number, line in enumerate(lines, 1):
                m = PIN.match(line)
                if not m or m.group(1).endswith("_min_version"):
                    continue
                pins += 1
                if not (number > 1 and SOURCE.match(lines[number - 2])):
                    missing.append(f"{rel}:{number}: {m.group(1)}")
        self.assertGreater(pins, 20)
        self.assertEqual(
            missing, [], "add `# upstream: <kind> <args>` above it (see scripts/deepops/upstream.py)"
        )


if __name__ == "__main__":
    unittest.main()
