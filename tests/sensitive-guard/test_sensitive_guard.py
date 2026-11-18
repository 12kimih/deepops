"""Tests for scripts/deepops/sensitive_guard.py against throwaway git repositories."""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

GUARD = Path(__file__).resolve().parents[2] / "scripts" / "deepops" / "sensitive_guard.py"
# Secret-shaped strings are assembled at run time so this file never trips the guard.
SLACK_TOKEN = "xo" + "xb-" + "1234567890-abcdefghij"
SITE_HOST = "login.site" + ".invalid"


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        site = self.repo / "config"
        site.mkdir()
        (site / "vars.yml").write_text(textwrap.dedent("""\
            other: 1
            user_map:
              alice01: U1
              'bob-x': U2
              shared: U3
            after: 2
            """))
        (site / "sensitive-patterns").write_text(textwrap.dedent(f"""\
            # comment
            literal:{SITE_HOST}==>cluster.example.com
            regex:(?<![0-9A-Za-z])48123(?![0-9A-Za-z])==>3001
            keys:vars.yml:user_map==>user
            allow:shared
            """))
        (self.repo / ".gitignore").write_text("config/\n")
        self.commit("base", {"README": "hello\n"})

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout

    def commit(self, message, files):
        for name, content in files.items():
            (self.repo / name).write_text(content)
            self.git("add", name)
        self.git("commit", "-q", "-m", message)

    def guard(self, *args, stdin=None, env=None):
        run = subprocess.run([sys.executable, str(GUARD), *args], cwd=self.repo, input=stdin, text=True,
                             capture_output=True, env={**os.environ, **(env or {})})
        return run.returncode, run.stdout + run.stderr

    def test_staged_clean_and_dirty(self):
        (self.repo / "a.txt").write_text("generic text\n")
        self.git("add", "a.txt")
        self.assertEqual(self.guard("staged")[0], 0)
        (self.repo / "b.txt").write_text(f"ok\nssh {SITE_HOST} -p 48123\n")
        self.git("add", "b.txt")
        code, out = self.guard("staged")
        self.assertEqual(code, 1)
        self.assertIn("b.txt:2: site rule 2", out)
        self.assertIn("b.txt:2: site rule 3", out)
        self.assertNotIn(SITE_HOST, out)

    def test_port_rule_ignores_longer_numbers(self):
        (self.repo / "c.txt").write_text("sha 1481232 and a48123b\n")
        self.git("add", "c.txt")
        self.assertEqual(self.guard("staged")[0], 0)

    def test_account_keys_are_words(self):
        (self.repo / "d.txt").write_text("alice012 bob-xy\n")
        self.git("add", "d.txt")
        self.assertEqual(self.guard("staged")[0], 0)
        (self.repo / "d2.txt").write_text("a shared account\n")
        self.git("add", "d2.txt")
        self.assertEqual(self.guard("staged")[0], 0)
        (self.repo / "e.txt").write_text("job of alice01 and bob-x\n")
        self.git("add", "e.txt")
        self.assertEqual(self.guard("staged")[0], 1)

    def test_builtin_secret_and_allow_pragma(self):
        (self.repo / "f.txt").write_text(f"token {SLACK_TOKEN}\n")
        self.git("add", "f.txt")
        code, out = self.guard("staged")
        self.assertEqual(code, 1)
        self.assertIn("Slack token", out)
        self.assertNotIn(SLACK_TOKEN, out)
        (self.repo / "f.txt").write_text(f"token {SLACK_TOKEN}  # sensitive-guard: allow\n")
        self.git("add", "f.txt")
        self.assertEqual(self.guard("staged")[0], 0)

    def test_file_name_is_checked(self):
        (self.repo / f"{SITE_HOST}.md").write_text("x\n")
        self.git("add", ".")
        code, out = self.guard("staged")
        self.assertEqual(code, 1)
        self.assertIn("(file name)", out)

    def test_message(self):
        msg = self.repo / "MSG"
        msg.write_text("feat: thing\n# 48123 in a comment is ignored\n")
        self.assertEqual(self.guard("message", str(msg))[0], 0)
        msg.write_text("feat: open port 48123\n")
        self.assertEqual(self.guard("message", str(msg))[0], 1)

    def test_history_and_push(self):
        base = self.git("rev-parse", "HEAD").strip()
        self.commit("add docs", {"g.txt": "fine\n"})
        self.assertEqual(self.guard("history", "HEAD", f"^{base}")[0], 0)
        self.commit(f"mention {SITE_HOST}", {"h.txt": "fine\n"})
        code, out = self.guard("history", "HEAD", f"^{base}")
        self.assertEqual(code, 1)
        self.assertIn("message:1: site rule 2", out)
        head = self.git("rev-parse", "HEAD").strip()
        stdin = f"refs/heads/main {head} refs/heads/main {base}\n"
        self.assertEqual(self.guard("push", stdin=stdin)[0], 1)
        env = {"PRE_COMMIT_FROM_REF": base, "PRE_COMMIT_TO_REF": head}
        self.assertEqual(self.guard("push", env=env)[0], 1)
        delete = f"(delete) {'0' * 40} refs/heads/old {base}\n"
        self.assertEqual(self.guard("push", stdin=delete)[0], 0)

    def test_tree(self):
        self.assertEqual(self.guard("tree")[0], 0)
        self.commit("bad", {"i.txt": "port 48123\n"})
        self.assertEqual(self.guard("tree")[0], 1)

    def test_no_site_and_missing_site_file(self):
        (self.repo / "j.txt").write_text("port 48123\n")
        self.git("add", "j.txt")
        self.assertEqual(self.guard("--no-site", "staged")[0], 0)
        self.assertEqual(self.guard("staged", env={"DEEPOPS_SENSITIVE_PATTERNS": str(self.repo / "nope")})[0], 0)
        self.assertEqual(self.guard("--patterns", str(self.repo / "nope"), "staged")[0], 2)

    def test_replace_text(self):
        code, out = self.guard("replace-text")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], f"{SITE_HOST}==>cluster.example.com")
        self.assertEqual(lines[1], "regex:(?<![0-9A-Za-z])48123(?![0-9A-Za-z])==>3001")
        self.assertTrue(lines[2].startswith("regex:") and "alice01" in lines[2] and lines[2].endswith("==>user") and "shared" not in lines[2])
        self.assertEqual(len(lines), 3)

    def test_bad_rule(self):
        for bad in ("nonsense\n", "regex:([==>x\n", "keys:vars.yml:missing==>x\n", "glob:*==>x\n"):
            (self.repo / "config" / "sensitive-patterns").write_text(bad)
            self.assertEqual(self.guard("tree")[0], 2, bad)


class HookManifestTest(unittest.TestCase):
    """The published hooks (.pre-commit-hooks.yaml) and this repository's own local hooks
    must run the guard the same way."""

    def test_local_hooks_match_published_hooks(self):
        import yaml
        root = GUARD.parents[2]
        published = {h["id"]: h for h in yaml.safe_load((root / ".pre-commit-hooks.yaml").read_text())}
        config = yaml.safe_load((root / ".pre-commit-config.yaml").read_text())
        local = {h["id"]: h for repo in config["repos"] if repo["repo"] == "local" for h in repo["hooks"]
                 if h["id"].startswith("sensitive-guard")}
        self.assertTrue(local)
        for hook_id, hook in local.items():
            self.assertIn(hook_id, published)
            for key in ("entry", "pass_filenames", "always_run"):
                self.assertEqual(hook.get(key), published[hook_id].get(key), f"{hook_id} {key}")
            self.assertEqual(hook.get("stages", ["pre-commit"]), published[hook_id]["stages"], hook_id)


if __name__ == "__main__":
    unittest.main()
