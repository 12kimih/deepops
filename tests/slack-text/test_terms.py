"""One name for a job whose allocated GPUs all sat idle, in every text the repository
keeps: Slack messages, DMs, comments, docs and tests. "A job not using GPUs" alone reads
as a CPU-only job (owner, 2026-11-27), so the Korean name says the GPUs were allocated
(cluster_tools.usage_report.IDLE_JOB, the one copy the tool's texts use) and the
English one says the job leaves its GPUs idle. This refuses the old phrasings anywhere
in the tracked tree."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
from cluster_tools import usage_report  # noqa: E402
from repo_scan import tracked  # noqa: E402

OLD = re.compile(
    "|".join(
        (
            r"(?<!할당받은 )GPU를 (?:\S+ ){0,2}쓰지 않",  # 'GPU를 쓰지 않는 잡', 'GPU를 6시간 동안 쓰지 않아'
            r"유휴 (?:GPU|잡)",
            r"(?i:\bidle[- ](?:GPU )?jobs?\b)",
        )
    )
)
SELF = Path(__file__).resolve()


class Terms(unittest.TestCase):
    def test_the_tool_names_it_with_allocated(self):
        self.assertEqual(usage_report.IDLE_JOB, "할당받은 GPU를 쓰지 않는 잡")
        self.assertIsNone(OLD.search(usage_report.IDLE_JOB))

    def test_no_old_phrasing_is_left(self):
        found, scanned = [], 0
        for path in tracked("."):
            if path.resolve() == SELF:
                continue
            try:
                text = path.read_text()
            except (UnicodeDecodeError, IsADirectoryError):
                continue
            scanned += 1
            for n, line in enumerate(text.splitlines(), 1):
                if OLD.search(line):
                    found.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
        self.assertGreater(scanned, 100, "the scan saw the tree")
        self.assertEqual(found, [], "say 'allocated' (Korean: 할당받은 GPU) or 'leaving its GPUs idle'")

    def test_the_pattern_catches_the_old_and_passes_the_new(self):
        for old in (
            "GPU를 쓰지 않는 잡",
            "GPU를 6시간 동안 쓰지 않아",
            "유휴 잡 취소",
            "idle GPU jobs",
            "Idle jobs:",
        ):
            self.assertRegex(old, OLD)
        for new in ("할당받은 GPU를 쓰지 않는 잡", "할당받은 GPU를 6시간 동안 쓰지 않아",
                    "jobs leaving their GPUs idle", "idle_hours", "an idle node"):  # fmt: skip
            self.assertNotRegex(new, OLD)


if __name__ == "__main__":
    unittest.main()
