"""scripts/deepops/notice_render.py, the notice-render hook the user docs run on every
commit: their notices.json, rendered as the bot posts it, or what it could not show."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/deepops/notice_render.py"
GOOD = {"notices": [{"id": "https://docs.example.org/news/qos/", "date": "2026-02-01", "text": "QoS",
                     "title": ":mega: QoS", "markdown": "**팀 QoS**로 제출해 주세요.",
                     "link": {"text": "자세히 보기", "url": "https://docs.example.org/news/qos/"},
                     "context": "정책  |  2026-02-01"}]}  # fmt: skip


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)


class NoticeRender(unittest.TestCase):
    def test_a_file_that_renders_passes_one_that_does_not_fails(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "notices.json")
            path.write_text(json.dumps(GOOD, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(run(str(path)).returncode, 0)
            bad = json.loads(json.dumps(GOOD))
            bad["notices"][0]["markdown"] = "*팀 QoS*로 제출해 주세요."
            path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            done = run(str(path))
            self.assertEqual(done.returncode, 1)
            self.assertIn("*팀 QoS*", done.stderr)

    def test_the_notices_a_command_prints(self):
        printed = f"import json; print(json.dumps({GOOD!r}))"
        self.assertEqual(run("--", sys.executable, "-c", printed).returncode, 0)
        failed = run("--", sys.executable, "-c", "import sys; sys.exit(3)")
        self.assertEqual(failed.returncode, 2)
        self.assertIn("failed (3)", failed.stderr)

    def test_usage(self):
        self.assertEqual(run().returncode, 2)

    def test_the_hook_is_offered(self):
        hooks = (ROOT / ".pre-commit-hooks.yaml").read_text()
        self.assertIn("id: notice-render", hooks)
        self.assertIn("entry: scripts/deepops/notice_render.py", hooks)


if __name__ == "__main__":
    unittest.main()
