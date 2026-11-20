"""scripts/deepops/no_silent_revert.py on throwaway repositories: a commit that takes a
file back to an older version of itself, undoing commits in between, is refused unless
it says it reverts; ordinary edits pass."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deepops" / "no_silent_revert.py"
ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


class Repo:
    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str, stdin: str | None = None) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.path, env=ENV, input=stdin, capture_output=True, text=True, check=True
        ).stdout.strip()

    def commit(self, message: str, files: dict[str, str | bytes | None]) -> str:
        for name, content in files.items():
            target = self.path / name
            if content is None:
                self.git("rm", "-q", name)
            else:
                target.write_bytes(content if isinstance(content, bytes) else content.encode())
                self.git("add", name)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def guard(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), *args],
            cwd=self.path,
            env=ENV,
            input=stdin,
            capture_output=True,
            text=True,
            errors="surrogateescape",
        )


class NoSilentRevertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(Path(self.tmp.name) / "r")

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_stale_copy_committed_on_top_is_refused(self):
        # 03c8ba9c's shape: B changes a file, C (built from a copy older than B) puts it back.
        r = self.repo
        r.commit("a: add the setting", {"vars.yml": "x: 1\n"})
        b = r.commit("b: another session moves it", {"vars.yml": "x: 2\n"})
        r.commit("c: an unrelated feature", {"vars.yml": "x: 1\n", "new.yml": "y\n"})
        out = r.guard("range", "HEAD~1..HEAD")
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("vars.yml goes back to its content before", out.stdout)
        self.assertIn(b[:12], out.stdout, "names the commit it would undo")

    def test_a_file_that_is_not_utf8_is_judged_not_a_traceback(self):
        # Latin-1 content and a Latin-1 file name: decoded strictly, the hook raised
        # UnicodeDecodeError and refused every push that touched such a file.
        r = self.repo
        name = "caf\udce9.txt"  # b"caf\xe9.txt" as os.fsdecode spells it
        r.commit("a", {name: b"caf\xe9 one\n"})
        b = r.commit("b", {name: b"caf\xe9 two\n"})
        r.commit("c", {name: b"caf\xe9 one\n"})
        out = r.guard("range", "HEAD~1..HEAD")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("goes back to its content before", out.stdout)
        self.assertIn(b[:12], out.stdout)
        self.assertIn(f"c: {name} goes back", out.stdout, "the name prints as its bytes")

    def test_a_stale_copy_with_own_edits_is_reported(self):
        # 2185112b's shape with an edit of its own: the file is new as a whole, but a
        # newer commit's block is gone again: reported, not refused (alone it is as often
        # a deliberate reversal).
        r = self.repo
        r.commit("a", {"hook.sh": "set -e\nrun\nend\n"})
        b = r.commit("b: read as committed", {"hook.sh": "set -e\nwork=1\ntrap x\nadd tree\nrun\nend\n"})
        r.commit("c: an unrelated tweak", {"hook.sh": "set -e\nrun\ndone\n"})
        out = r.guard("range", "HEAD~1..HEAD")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn(f"hook.sh undoes a change of {b[:12]}", out.stdout)

    def test_rewriting_recent_lines_differently_passes(self):
        r = self.repo
        r.commit("a", {"f": "one\ntwo\n"})
        r.commit("b", {"f": "one\nx1\nx2\nx3\ntwo\n"})
        r.commit("c: a better version", {"f": "one\ny1\ny2\ntwo\n"})
        self.assertEqual(r.guard("range", "HEAD~1..HEAD").returncode, 0)

    def test_a_push_after_the_remote_moved_is_refused(self):
        r = self.repo
        r.commit("a: base", {"f": "1\n"})
        remote = r.commit("b: remote moves on", {"f": "2\n"})
        local = r.commit("c: squashed onto the remote from a stale tree", {"f": "1\n", "g": "new\n"})
        out = r.guard("push", stdin=f"refs/heads/main {local} refs/heads/main {remote}\n")
        self.assertEqual(out.returncode, 1, out.stdout)

    def test_an_ordinary_edit_passes(self):
        r = self.repo
        r.commit("a", {"f": "1\n"})
        r.commit("b", {"f": "2\n"})
        r.commit("c: a new value", {"f": "3\n"})
        self.assertEqual(r.guard("range", "HEAD~2..HEAD").returncode, 0)

    def test_a_declared_revert_passes(self):
        r = self.repo
        r.commit("a", {"f": "1\n"})
        b = r.commit("b", {"f": "2\n"})
        r.git("revert", "--no-edit", b)  # "This reverts commit <b>."
        r.git("revert", "--no-edit", "HEAD")  # Reapply, naming the revert
        reapply = r.git("rev-parse", "HEAD")
        r.commit(f"e: take b out again, undoing {reapply[:9]}", {"f": "1\n"})
        self.assertEqual(r.guard("range", "HEAD~3..HEAD").returncode, 0)

    def test_a_word_alone_does_not_declare_a_revert(self):
        # Backup messages say "restore": only naming the undone commit counts.
        r = self.repo
        r.commit("a", {"f": "1\n"})
        r.commit("b", {"f": "2\n"})
        r.commit("c: restore the backup timer", {"f": "1\n"})
        self.assertEqual(r.guard("range", "HEAD~1..HEAD").returncode, 1)

    def test_a_merge_that_takes_a_stale_side_is_refused(self):
        r = self.repo
        r.commit("a", {"f": "1\n"})
        r.git("checkout", "-q", "-b", "side")
        r.commit("side: unrelated", {"g": "x\n"})
        r.git("checkout", "-q", "main")
        r.commit("b: main moves on", {"f": "2\n"})
        r.git("merge", "-q", "--no-ff", "--no-commit", "side")
        (r.path / "f").write_text("1\n")  # a conflict resolved with the stale copy
        r.git("add", "f")
        r.git("commit", "-q", "-m", "merge side")
        out = r.guard("range", "HEAD^..HEAD")
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("f goes back to its content before", out.stdout)

    def test_an_ordinary_merge_passes(self):
        r = self.repo
        r.commit("a", {"f": "1\n"})
        r.git("checkout", "-q", "-b", "side")
        r.commit("side: g", {"g": "x\n"})
        r.git("checkout", "-q", "main")
        r.commit("b: main moves on", {"f": "2\n"})
        r.git("merge", "-q", "--no-ff", "-m", "merge side", "side")
        self.assertEqual(r.guard("range", "HEAD^..HEAD").returncode, 0)

    def test_a_stale_tree_also_lists_the_newer_files_it_drops(self):
        r = self.repo
        r.commit("a", {"f": "1\n"})
        added = r.commit("b: a new role and a change", {"role.yml": "r\n", "f": "2\n"})
        r.commit("c: built on a, committed on top of b", {"role.yml": None, "f": "1\n"})
        out = r.guard("range", "HEAD~1..HEAD")
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn(f"role.yml is deleted, added by {added[:12]}", out.stdout)

    def test_a_stale_tree_that_only_adds_and_deletes_files_is_refused(self):
        r = self.repo
        r.commit("base", {"gone.txt": "old gone\n"})
        r.commit("peer: add new.txt, drop gone.txt", {"new.txt": "n\n", "gone.txt": None})
        stale = r.commit(
            "mine on a stale copy", {"gone.txt": "old gone\n", "new.txt": None, "mine.txt": "m\n"}
        )
        out = r.guard("range", f"{stale}~1..{stale}")
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("gone.txt comes back as it was before", out.stdout)
        self.assertIn("new.txt is deleted", out.stdout)

    def test_a_new_file_of_the_same_name_passes(self):
        r = self.repo
        r.commit("base", {"gone.txt": "old gone\n"})
        r.commit("drop gone.txt", {"gone.txt": None})
        fresh = r.commit("a new gone.txt", {"gone.txt": "something else\n"})
        self.assertEqual(r.guard("range", f"{fresh}~1..{fresh}").returncode, 0)

    def test_deleting_a_recent_file_alone_passes(self):
        # The end of a refactor: the file moved, nothing else went back.
        r = self.repo
        r.commit("a", {"keep": "k\n"})
        r.commit("b: a helper", {"helper.py": "h\n"})
        r.commit("c: the helper moves into the package", {"helper.py": None, "pkg.py": "h\n"})
        self.assertEqual(r.guard("range", "HEAD~1..HEAD").returncode, 0)

    def test_a_value_switched_back_must_say_so(self):
        # A one-line value going back to an older setting is a step back too: refused until
        # the message says it, with the commit it undoes printed.
        r = self.repo
        r.commit("a", {"switch": "true\n"})
        r.commit("b: turn it off", {"switch": "false\n"})
        r.commit("c: turn it on again", {"switch": "true\n"})
        self.assertEqual(r.guard("range", "HEAD~1..HEAD").returncode, 1)
        d = r.commit("d: off", {"switch": "false\n"})
        r.commit(f"e: on again, undoing {d[:8]}", {"switch": "true\n"})
        self.assertEqual(r.guard("range", "HEAD~1..HEAD").returncode, 0)


if __name__ == "__main__":
    unittest.main()
