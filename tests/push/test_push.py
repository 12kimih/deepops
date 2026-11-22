"""scripts/push.sh: checkouts of one clone check and push one at a time.

Each test builds a throwaway origin (bare), a clone with a worktree beside it, and a
stub scripts/check.sh, and runs the real push.sh copied into the clone.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import bounds  # noqa: E402

PUSH = ROOT / "scripts" / "push.sh"
ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True, timeout=bounds.CHILD
    ).stdout.strip()


class Push(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.origin = self.tmp / "origin.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.clone = self.tmp / "clone"
        git(self.tmp, "clone", "-q", str(self.origin), str(self.clone))
        (self.clone / "scripts").mkdir()
        shutil.copy(PUSH, self.clone / "scripts" / "push.sh")
        self.check(self.clone, "true")
        git(self.clone, "add", "-A")
        git(self.clone, "commit", "-q", "-m", "base")
        git(self.clone, "push", "-q", "origin", "HEAD:main")
        self.wt = self.tmp / "wt"
        git(self.clone, "worktree", "add", "-q", "--detach", str(self.wt), "origin/main")

    def check(self, checkout, body):
        path = checkout / "scripts" / "check.sh"
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)

    def commit(self, checkout, name):
        (checkout / name).write_text(name)
        git(checkout, "add", name)
        git(checkout, "commit", "-q", "-m", name)

    def push(self, checkout, **kwargs):
        return subprocess.run(
            ["bash", "scripts/push.sh"], cwd=checkout, env=ENV, capture_output=True, text=True,
            timeout=bounds.CHILD, **kwargs,
        )  # fmt: skip

    def origin_files(self):
        return sorted(git(self.origin, "ls-tree", "--name-only", "main").split())

    def test_rebases_checks_and_pushes(self):
        self.commit(self.clone, "a")
        git(self.clone, "push", "-q", "origin", "HEAD:main")
        self.commit(self.wt, "b")
        out = self.push(self.wt)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("pushed", out.stdout)
        self.assertEqual(self.origin_files(), ["a", "b", "scripts"])
        self.assertIn("nothing to push", self.push(self.wt).stdout)

    def test_a_failed_check_pushes_nothing(self):
        self.commit(self.wt, "b")
        self.check(self.wt, "exit 1")
        git(self.wt, "commit", "-q", "-am", "failing check")
        self.assertNotEqual(self.push(self.wt).returncode, 0)
        self.assertEqual(self.origin_files(), ["scripts"])

    def test_uncommitted_changes_are_refused(self):
        (self.wt / "loose").write_text("x")
        out = self.push(self.wt)
        self.assertEqual(out.returncode, 2)
        self.assertIn("uncommitted changes", out.stderr)

    def test_a_second_checkout_waits_for_the_first(self):
        lock = Path(git(self.clone, "rev-parse", "--absolute-git-dir")) / "deepops-push.lock"
        lock.write_text("pid 1, someone, /elsewhere, since then\n")
        holder = subprocess.Popen(
            ["flock", str(lock), "sleep", "3"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.addCleanup(holder.kill)
        time.sleep(0.5)
        self.commit(self.wt, "b")
        started = time.monotonic()
        out = self.push(self.wt)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("waiting for pid 1, someone", out.stderr)
        self.assertGreater(time.monotonic() - started, 1.5)
        self.assertEqual(self.origin_files(), ["b", "scripts"])

    def test_origin_moving_during_the_check_checks_again(self):
        # The first check pushes another commit to origin from a second clone, as a push
        # made without push.sh would; the script rebases onto it and checks again.
        other = self.tmp / "other"
        git(self.tmp, "clone", "-q", str(self.origin), str(other))
        self.commit(other, "c")
        flag = self.tmp / "moved"
        self.check(
            self.wt,
            f"[ -e {flag} ] || {{ touch {flag}; git -C {other} push -q origin HEAD:main; }}",
        )
        git(self.wt, "commit", "-q", "-am", "check that moves origin once")
        self.commit(self.wt, "b")
        out = self.push(self.wt)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("moved during the check", out.stderr)
        self.assertEqual(self.origin_files(), ["b", "c", "scripts"])


if __name__ == "__main__":
    unittest.main()
