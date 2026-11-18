"""Keep the tree a fixed point of scripts/deepops/normalize-ansible-style.py.

Upstream merges are cheap only while the fork's own files already match what
the normalizer produces from upstream code, so a file that drifts from it is
caught here rather than as a conflict in the next merge.
"""

from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]


class AnsibleStyleTests(unittest.TestCase):
    def test_tree_is_normalized(self):
        result = subprocess.run(
            [sys.executable, "scripts/deepops/normalize-ansible-style.py", "--check", "."],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, "run scripts/deepops/normalize-ansible-style.py on:\n" + result.stdout)


if __name__ == "__main__":
    unittest.main()
