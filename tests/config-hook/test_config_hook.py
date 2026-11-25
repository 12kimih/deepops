"""scripts/deepops/config-pre-commit.sh judges the site's pages by the deepops they will
describe: origin's main (what sync.py brings in), or DEEPOPS_REV when the caller names
it (push.sh, committing the pages a deepops change regenerates). Never the clone's own
HEAD: the shared clone stays behind main until the owner syncs it, and judging by it
refused every regenerated page while deepops' check demanded one (2026-11-26).

A throwaway deepops (the real hook and export-tree.sh, stand-in checks: its "facts"
file is what the site's page must state) and a site config with the hook installed as
scripts/setup.sh installs it.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import bounds  # noqa: E402

ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "DEEPOPS_"))},
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}

# Stand-ins for the hook's two checks. generate.py --check --site: the committed page
# (DEEPOPS_SITE_REV, the staged tree) states this deepops' facts.
LINT = "import sys\nsys.exit(0)\n"
GENERATE = """import os, pathlib, subprocess, sys
site, tree = os.environ["DEEPOPS_CONFIG_DIR"], os.environ["DEEPOPS_SITE_REV"]
page = subprocess.run(["git", "-C", site, "show", tree + ":page"], capture_output=True, text=True).stdout
if page != pathlib.Path("facts").read_text():
    print("stale: config/page")
    sys.exit(1)
"""


def git(cwd, *args, env=None):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env or ENV,
        check=True,
        capture_output=True,
        text=True,
        timeout=bounds.CHILD,
    ).stdout.strip()


class ConfigHook(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        origin = self.tmp / "deepops.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(origin))
        self.shared = self.tmp / "deepops"
        git(self.tmp, "clone", "-q", str(origin), str(self.shared))
        scripts = self.shared / "scripts"
        (scripts / "deepops").mkdir(parents=True)
        (scripts / "docs").mkdir()
        for name in ("config-pre-commit.sh", "export-tree.sh"):
            shutil.copy(ROOT / "scripts" / "deepops" / name, scripts / "deepops" / name)
        (scripts / "deepops" / "config_lint.py").write_text(LINT)
        (scripts / "docs" / "generate.py").write_text(GENERATE)
        (self.shared / "facts").write_text("old\n")
        git(self.shared, "add", "-A")
        git(self.shared, "commit", "-q", "-m", "deepops")
        git(self.shared, "push", "-q", "origin", "HEAD:main")
        (self.shared / ".venv" / "bin").mkdir(parents=True)
        (self.shared / ".venv" / "bin" / "python").symlink_to(sys.executable)
        # main moves on; the shared clone stays behind until someone syncs it
        peer = self.tmp / "peer"
        git(self.tmp, "clone", "-q", str(origin), str(peer))
        (peer / "facts").write_text("new\n")
        git(peer, "commit", "-q", "-am", "facts change")
        git(peer, "push", "-q", "origin", "HEAD:main")
        self.site = self.tmp / "config"
        git(self.tmp, "init", "-q", "-b", "main", str(self.site))
        hook = self.site / ".git" / "hooks" / "pre-commit"
        hook.symlink_to(self.shared / "scripts" / "deepops" / "config-pre-commit.sh")

    def commit_page(self, text, env=None):
        (self.site / "page").write_text(text)
        git(self.site, "add", "page")
        return subprocess.run(
            ["git", "commit", "-q", "-m", "page"], cwd=self.site, env=env or ENV,
            capture_output=True, text=True, timeout=bounds.CHILD,
        )  # fmt: skip

    def test_the_page_is_judged_by_main_not_the_clone_behind_it(self):
        self.assertEqual((self.shared / "facts").read_text(), "old\n", "the clone is behind")
        refused = self.commit_page("old\n")
        self.assertNotEqual(refused.returncode, 0, "the clone's HEAD states what main no longer does")
        self.assertIn("stale: config/page", refused.stdout + refused.stderr)
        done = self.commit_page("new\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual((self.shared / "facts").read_text(), "old\n", "the clone did not move")

    def test_a_named_revision_judges_instead(self):
        (self.shared / "facts").write_text("next\n")
        git(self.shared, "commit", "-q", "-am", "an unpushed change")
        rev = git(self.shared, "rev-parse", "HEAD")
        self.assertNotEqual(self.commit_page("next\n").returncode, 0, "main does not state it")
        done = self.commit_page("next\n", env={**ENV, "DEEPOPS_REV": rev})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_revision_the_clone_lacks_is_said(self):
        out = self.commit_page("new\n", env={**ENV, "DEEPOPS_REV": "0" * 40})
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("is not in", out.stderr)


if __name__ == "__main__":
    unittest.main()
