"""The Run:ai role's minimums (the GPU Operator, the driver branch, the monitoring
chart) are named where they are explained, never restated: AGENTS.md, the guide, the
skill, the example config and the role's own messages each copied v26.7.1 and r615, and
a raised minimum would have left five places stating the old one."""

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

MINIMUM = re.compile(r"_min_(version|driver_major)$")
WHERE = ["AGENTS.md", "docs/k8s-cluster/runai.md", "skills/deploy-runai-kai", "config.example", "roles/runai"]


class MinimumsNamed(unittest.TestCase):
    def test_no_minimum_is_restated(self):
        defaults_path = ROOT / "roles/runai/defaults/main.yml"
        minimums = {
            k: str(v) for k, v in yaml.safe_load(defaults_path.read_text()).items() if MINIMUM.search(k)
        }
        self.assertGreaterEqual(len(minimums), 3, sorted(minimums))
        offenders = []
        for path in tracked(*WHERE):
            if path == defaults_path or not path.is_file():
                continue
            text = path.read_text(errors="ignore")
            for name, value in minimums.items():
                bare = re.escape(value.lstrip("v"))
                for m in re.finditer(rf"(?<![\w.])[vr]?{bare}(?![\w.])", text):
                    line = text.count("\n", 0, m.start()) + 1
                    offenders.append(f"{path.relative_to(ROOT)}:{line}: {m.group(0)} (name {name})")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
