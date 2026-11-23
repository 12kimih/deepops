"""scripts/push.sh: checkouts of one clone check at the same time and push one at a time;
what is pushed is always what was checked.

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


class Base(unittest.TestCase):
    """A throwaway origin, a clone with a worktree beside it, and the real push.sh."""

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

    def push(self, checkout, env=None, **kwargs):
        return subprocess.run(
            ["bash", "scripts/push.sh"], cwd=checkout, env=env or ENV, capture_output=True, text=True,
            timeout=bounds.CHILD, **kwargs,
        )  # fmt: skip

    def origin_files(self):
        return sorted(git(self.origin, "ls-tree", "--name-only", "main").split())


class Push(Base):
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
        # The holder keeps the lock until this checkout's check has run, then 2 s more:
        # the check needs no lock, the push does.
        checked = self.tmp / "checked"
        holder = subprocess.Popen(
            ["flock", str(lock), "sh", "-c", f"until [ -e {checked} ]; do sleep 0.1; done; sleep 2"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        self.addCleanup(holder.kill)
        while not lock.exists() or subprocess.run(["flock", "-n", str(lock), "true"]).returncode == 0:
            time.sleep(0.1)
        self.check(self.wt, f"touch {checked}")
        git(self.wt, "commit", "-q", "-am", "check that marks it ran")
        self.commit(self.wt, "b")
        out = self.push(self.wt)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("checked; waiting to push for pid 1, someone", out.stderr)
        self.assertIsNotNone(holder.poll(), "pushed only after the holder let go")
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
        self.assertIn("origin moved since the check", out.stderr)
        self.assertEqual(self.origin_files(), ["b", "c", "scripts"])

    def test_checkouts_check_at_once_and_the_later_push_checks_again_on_the_first(self):
        # Each check waits until both have started (the old script held the lock through
        # the check, so the second never started and the first gave up) and logs a run.
        log = self.tmp / "log"
        log.mkdir()
        self.check(
            self.clone,
            f"""touch {log}/$(basename "$PWD").started
i=0; while [ "$(ls {log} | grep -c started)" -lt 2 ]; do i=$((i+1)); [ $i -gt 100 ] && exit 1; sleep 0.1; done
echo "$(basename "$PWD") $(git rev-parse HEAD^)" >> {log}/runs""",
        )
        git(self.clone, "commit", "-q", "-am", "check that needs a peer")
        git(self.clone, "push", "-q", "origin", "HEAD:main")
        wt2 = self.tmp / "wt2"
        git(self.clone, "worktree", "add", "-q", "--detach", str(wt2), "origin/main")
        git(self.wt, "checkout", "-q", "--detach", "origin/main")
        self.commit(self.wt, "b")
        self.commit(wt2, "c")
        runs = [subprocess.Popen(["bash", "scripts/push.sh"], cwd=c, env=ENV, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True) for c in (self.wt, wt2)]  # fmt: skip
        outs = [p.communicate(timeout=bounds.CHILD) for p in runs]
        self.assertEqual([p.returncode for p in runs], [0, 0], outs)
        self.assertEqual(self.origin_files(), ["b", "c", "scripts"])
        lines = (log / "runs").read_text().splitlines()
        self.assertEqual(len(lines), 3, "one check each, and one again for the later push")
        first = git(self.origin, "rev-parse", "main~1")
        self.assertEqual(lines[-1].split()[1], first, "the later push was checked on the first push")
        self.assertEqual(sum("origin moved since the check" in err for _, err in outs), 1)

    def test_an_unmoved_origin_pushes_without_checking_again(self):
        runs = self.tmp / "runs"
        self.check(self.wt, f"echo run >> {runs}")
        git(self.wt, "commit", "-q", "-am", "counting check")
        out = self.push(self.wt)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(runs.read_text(), "run\n")


class SiteConfig(Base):
    """The check reads the site config at its origin's main, with the checkout's own
    unpushed commits on top, and never moves the checkout (a deploy may run from it)."""

    def setUp(self):
        super().setUp()
        self.config_origin = self.tmp / "config.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.config_origin))
        self.config = self.tmp / "config"
        git(self.tmp, "clone", "-q", str(self.config_origin), str(self.config))
        (self.config / "setting").write_text("old\n")
        git(self.config, "add", "setting")
        git(self.config, "commit", "-q", "-m", "config base")
        git(self.config, "push", "-q", "origin", "HEAD:main")
        self.seen = self.tmp / "seen"
        self.check(self.wt, f'cat "$DEEPOPS_CONFIG_DIR/setting" > {self.seen}')
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", "check reads the config")

    def push_with_config(self):
        return self.push(self.wt, env={**ENV, "DEEPOPS_CONFIG_DIR": str(self.config)})

    def land_in_config(self, value):
        peer = self.tmp / "config-peer"
        if not peer.exists():
            git(self.tmp, "clone", "-q", str(self.config_origin), str(peer))
        git(peer, "pull", "-q", "--ff-only")
        (peer / "setting").write_text(value)
        git(peer, "commit", "-q", "-am", "peer")
        git(peer, "push", "-q", "origin", "HEAD:main")

    def test_a_config_change_a_peer_landed_is_checked_and_the_checkout_stays(self):
        self.land_in_config("new\n")
        head = git(self.config, "rev-parse", "HEAD")
        out = self.push_with_config()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.seen.read_text(), "new\n")
        self.assertEqual(git(self.config, "rev-parse", "HEAD"), head, "the checkout did not move")
        self.assertEqual((self.config / "setting").read_text(), "old\n")
        self.assertEqual(git(self.config, "worktree", "list").count("\n"), 0, "the throwaway tree is gone")

    def test_a_config_change_landed_during_the_check_is_checked_before_the_push(self):
        peer = self.tmp / "config-peer"
        git(self.tmp, "clone", "-q", str(self.config_origin), str(peer))
        (peer / "setting").write_text("new\n")
        git(peer, "commit", "-q", "-am", "peer")
        flag = self.tmp / "landed"
        self.check(self.wt, f'cat "$DEEPOPS_CONFIG_DIR/setting" >> {self.seen}; '
                            f"[ -e {flag} ] || {{ touch {flag}; git -C {peer} push -q origin HEAD:main; }}")  # fmt: skip
        git(self.wt, "commit", "-q", "-am", "check that lands a config change once")
        out = self.push_with_config()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("origin moved since the check", out.stderr)
        self.assertEqual(self.seen.read_text(), "old\nnew\n", "checked again with the landed config")

    def test_an_unpushed_config_commit_is_checked_on_top(self):
        self.land_in_config("new\n")
        (self.config / "other").write_text("mine\n")
        git(self.config, "add", "other")
        git(self.config, "commit", "-q", "-m", "my half of a pair")
        self.check(self.wt, f'cat "$DEEPOPS_CONFIG_DIR/setting" "$DEEPOPS_CONFIG_DIR/other" > {self.seen}')
        git(self.wt, "commit", "-q", "-am", "check reads both")
        out = self.push_with_config()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.seen.read_text(), "new\nmine\n")

    def test_uncommitted_config_edits_are_not_read(self):
        (self.config / "setting").write_text("half done\n")
        out = self.push_with_config()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.seen.read_text(), "old\n")
        self.assertEqual((self.config / "setting").read_text(), "half done\n")

    def test_a_config_commit_that_does_not_rebase_is_refused(self):
        self.land_in_config("theirs\n")
        (self.config / "setting").write_text("mine\n")
        git(self.config, "commit", "-q", "-am", "conflicting")
        out = self.push_with_config()
        self.assertEqual(out.returncode, 2)
        self.assertIn("do not rebase", out.stderr)
        self.assertEqual(self.origin_files(), ["scripts"], "nothing pushed")


if __name__ == "__main__":
    unittest.main()
