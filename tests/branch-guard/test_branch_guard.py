"""scripts/deepops/branch-guard.sh as the reference-transaction hook of a throwaway
repository with a second worktree: a branch checked out in one worktree cannot be moved
from another, and every ordinary ref update passes."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "scripts" / "deepops" / "branch-guard.sh"
ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True, text=True)


class BranchGuard(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.main = Path(tmp.name) / "shared"
        self.other = Path(tmp.name) / "private"
        self.main.mkdir()
        git(self.main, "init", "-q", "-b", "main")
        git(self.main, "commit", "-q", "--allow-empty", "-m", "one")
        (self.main / ".git" / "hooks" / "reference-transaction").symlink_to(GUARD)
        self.assertEqual(git(self.main, "worktree", "add", "-q", "--detach", str(self.other)).returncode, 0)
        self.head = git(self.main, "rev-parse", "main").stdout.strip()

    def moved(self) -> bool:
        return git(self.main, "rev-parse", "main").stdout.strip() != self.head

    def test_another_worktree_cannot_reset_the_branch(self):
        git(self.other, "commit", "-q", "--allow-empty", "-m", "two")
        result = git(self.other, "update-ref", "refs/heads/main", "HEAD")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checked out in", result.stderr)
        self.assertFalse(self.moved())

    def test_another_worktree_cannot_check_it_out_with_B(self):
        git(self.other, "commit", "-q", "--allow-empty", "-m", "two")
        self.assertNotEqual(git(self.other, "checkout", "-q", "-B", "main").returncode, 0)
        self.assertFalse(self.moved())

    def test_the_owning_worktree_commits(self):
        self.assertEqual(git(self.main, "commit", "-q", "--allow-empty", "-m", "two").returncode, 0)
        self.assertTrue(self.moved())

    def test_detached_work_and_free_branches_pass(self):
        self.assertEqual(git(self.other, "commit", "-q", "--allow-empty", "-m", "two").returncode, 0)
        self.assertEqual(git(self.other, "branch", "topic").returncode, 0)
        self.assertEqual(git(self.other, "branch", "-f", "topic", "HEAD~1").returncode, 0)
        self.assertFalse(self.moved())


if __name__ == "__main__":
    unittest.main()
