"""scripts/deepops/sync.py moves the shared checkouts only forward, only when clean, and
never while a deploy holds its lock. Temporary repositories and lock directories only."""

import fcntl
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402

sync = in_tree.load("scripts/deepops/sync.py")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def commit(repo, message):
    git(
        repo,
        "-c",
        "user.email=t@example.org",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        message,
    )


class Locks(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.base = self.dir.name

    def tearDown(self):
        self.dir.cleanup()

    def test_a_running_deploy_is_named_and_nothing_is_held(self):
        path = os.path.join(self.dir.name, "a.lock")
        with open(path, "w") as f:
            f.write("pid 1, a deploy")
            f.flush()
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held, busy = sync.deploy_lock.hold_all("test", base=self.base)
            self.assertEqual(busy, ["pid 1, a deploy"])
            for fd in held:
                os.close(fd)

    def test_no_deploy_holds_every_lock(self):
        Path(self.dir.name, "a.lock").touch()
        held, busy = sync.deploy_lock.hold_all("test", base=self.base)
        self.assertEqual((len(held), busy), (1, []))
        for fd in held:
            os.close(fd)


class Forward(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.origin, self.clone = base / "origin.git", base / "clone"
        seed = base / "seed"
        subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
        commit(seed, "one")
        subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(self.origin)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.clone)], check=True)
        self.seed = seed
        git(seed, "remote", "add", "origin", str(self.origin))

    def tearDown(self):
        self.tmp.cleanup()

    def advance(self):
        commit(self.seed, "two")
        git(self.seed, "push", "-q", "origin", "main")

    def test_moves_forward(self):
        self.advance()
        self.assertIn("would move", sync.forward(self.clone, dry_run=True))
        self.assertIn("moved", sync.forward(self.clone, dry_run=False))
        self.assertIn("up to date", sync.forward(self.clone, dry_run=False))

    def test_changed_files_are_left_alone(self):
        Path(self.clone, "f").write_text("x")
        git(self.clone, "add", "f")
        self.advance()
        self.assertIn("left alone", sync.forward(self.clone, dry_run=False))

    def test_local_commits_are_left_alone(self):
        commit(self.clone, "mine")
        self.advance()
        self.assertIn("has commits", sync.forward(self.clone, dry_run=False))


if __name__ == "__main__":
    unittest.main()
