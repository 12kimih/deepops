"""Tests for roles/ai_agent_rules/files/ai_agent_rules.py (no network: file:// URLs)."""

import importlib.util
import io
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "roles/ai_agent_rules/files/ai_agent_rules.py"
spec = importlib.util.spec_from_file_location("ai_agent_rules", SCRIPT)
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)

TEXT = rules.MARKER + '\n# 규칙\n\n- "따옴표"와 \\역슬래시, \'\'\' 그리고 """ 도 안전하게\n'


class RefreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.src = self.dir / "AGENTS.md"
        self.claude = self.dir / "etc/claude-code/CLAUDE.md"
        self.codex = self.dir / "etc/codex/requirements.toml"

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, url=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = rules.main(
                ["--url", url or self.src.as_uri(), "--claude", str(self.claude), "--codex", str(self.codex)]
            )
        return code, out.getvalue() + err.getvalue()

    def test_installs_both_channels(self):
        self.src.write_text(TEXT)
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("installed", out)
        self.assertEqual(self.claude.read_text(), TEXT)
        parsed = tomllib.loads(self.codex.read_text())
        self.assertEqual(parsed, {"additional_developer_instructions": TEXT})
        self.assertEqual(oct(self.claude.stat().st_mode & 0o777), "0o644")

    def test_unchanged_rules_are_not_rewritten(self):
        self.src.write_text(TEXT)
        self.run_main()
        mtime = self.codex.stat().st_mtime_ns
        code, out = self.run_main()
        self.assertEqual((code, out.strip()), (0, "rules unchanged"))
        self.assertEqual(self.codex.stat().st_mtime_ns, mtime)

    def test_failed_download_keeps_installed_rules(self):
        self.src.write_text(TEXT)
        self.run_main()
        code, out = self.run_main(url=(self.dir / "gone.md").as_uri())
        self.assertEqual(code, 0)
        self.assertIn("kept", out)
        self.assertEqual(self.claude.read_text(), TEXT)

    def test_nothing_installed_and_download_fails(self):
        code, _ = self.run_main(url=(self.dir / "gone.md").as_uri())
        self.assertEqual(code, 1)
        self.assertFalse(self.claude.exists())

    def test_refuses_wrong_or_oversized_content(self):
        self.src.write_text("<html>not the rules</html>")
        self.assertEqual(self.run_main()[0], 1)
        self.src.write_text(rules.MARKER + "x" * rules.MAX_BYTES)
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("larger", out)
        self.assertFalse(self.codex.exists())


if __name__ == "__main__":
    unittest.main()
