"""config_lint.py: telling comments from commented-out values, and each check."""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("config_lint", ROOT / "scripts" / "deepops" / "config_lint.py")
cl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cl)


class CommentText(unittest.TestCase):
    def test_comments(self):
        self.assertEqual(cl.comment_text("# Hosts to set up"), "# Hosts to set up")
        self.assertEqual(cl.comment_text("#"), "#")
        self.assertEqual(cl.comment_text("  # nested"), "# nested")

    def test_commented_values_are_not_comments(self):
        for line in ("#  - README", "#    key: x", "#key: x", "#  default-address-pools:"):
            self.assertIsNone(cl.comment_text(line), line)

    def test_a_comment_inside_a_commented_mapping_is_a_comment(self):
        self.assertEqual(cl.comment_text("#  # The name the commands print"), "# The name the commands print")


class Drift(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        for d in ("example/group_vars", "site/group_vars"):
            (base / d).mkdir(parents=True)
        self.example, self.site = base / "example", base / "site"
        patch = mock.patch.multiple(cl, EXAMPLE=self.example, CONFIG=self.site)
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, example, site):
        (self.example / "group_vars/all.yml").write_text(example)
        (self.site / "group_vars/all.yml").write_text(site)

    def test_same_comments_with_values_set_is_clean(self):
        self.write("# What x is.\n#x: 1\n#list:\n#  - a\n", "# What x is.\nx: 2\nlist:\n  - b\n")
        self.assertEqual(cl.check_drift(), [])

    def test_site_comments_are_exempt(self):
        self.write("# What x is.\n#x: 1\n", "# What x is.\n# site: why this site sets 2\nx: 2\n")
        self.assertEqual(cl.check_drift(), [])

    def test_changed_comment_and_extra_name_are_reported(self):
        self.write("# What x is.\n#x: 1\n", "# Something else.\nx: 2\ny: 3\n")
        problems = cl.check_drift()
        self.assertTrue(any("comments differ" in p for p in problems))
        self.assertTrue(any("config line 3" in p for p in problems))
        self.assertFalse(any("Something else" in p for p in problems), "config text must not be printed")

    def test_under_the_commit_hook_the_site_is_read_as_committed(self):
        # pre-commit stashes this repository's unstaged edits, never the site's: a
        # half-done site edit must not fail another commit, nor a committed shadow hide.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(self.site)]
        self.write("# What x is.\n#x: 1\n", "# What x is.\nx: 2\n")
        import subprocess

        subprocess.run(git[:-2] + ["init", "-q", str(self.site)], check=True, env=env)
        subprocess.run(git + ["add", "-A"], check=True, env=env)
        subprocess.run(git + ["commit", "-qm", "c"], check=True, env=env)
        (self.site / "group_vars/all.yml").write_text("# Half done.\nx: 2\ny: 1\n")
        (self.site / "group_vars/all").mkdir()
        by_hand = {k: v for k, v in os.environ.items() if k != "PRE_COMMIT"}
        with mock.patch.dict(os.environ, by_hand, clear=True):
            self.assertTrue(cl.check_drift())
        with mock.patch.dict(os.environ, {"PRE_COMMIT": "1"}):
            self.assertEqual(cl.check_drift(), [])
            self.assertEqual(cl.check_shadow(), [])


class Repository(unittest.TestCase):
    def test_this_repository_is_clean(self):
        with mock.patch.dict(os.environ, {}):
            self.assertEqual(cl.check_dead() + cl.check_doc_paths(), [])


class Shadow(unittest.TestCase):
    """Ansible 2.22 checked: all/ beside all.yml, all.yml beside all.yaml, and
    host_vars/h/ beside h.yml each load only one of the two, silently."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.example, self.site = base / "example", base / "site"
        for d in (self.example / "group_vars", self.site / "group_vars", self.site / "host_vars"):
            d.mkdir(parents=True)
        patch = mock.patch.multiple(cl, EXAMPLE=self.example, CONFIG=self.site)
        patch.start()
        self.addCleanup(patch.stop)

    def test_one_place_each_is_clean(self):
        (self.site / "group_vars/all.yml").write_text("a: 1\n")
        (self.site / "group_vars/slurm").mkdir()
        (self.site / "host_vars/gpu1.yml").write_text("b: 1\n")
        self.assertEqual(cl.check_shadow(), [])

    def test_two_places_are_reported(self):
        (self.site / "group_vars/all.yml").write_text("a: 1\n")
        (self.site / "group_vars/all").mkdir()
        (self.example / "group_vars/k8s.yml").write_text("")
        (self.example / "group_vars/k8s.yaml").write_text("")
        (self.site / "host_vars/gpu1").mkdir()
        (self.site / "host_vars/gpu1.yml").write_text("")
        found = cl.check_shadow()
        self.assertEqual(len(found), 3, found)
        self.assertTrue(any("config/group_vars: all/, all.yml" in f for f in found), found)
        self.assertTrue(any("config.example/group_vars: k8s.yaml, k8s.yml" in f for f in found), found)

    def test_this_repository_is_clean(self):
        with mock.patch.multiple(cl, EXAMPLE=ROOT / "config.example", CONFIG=ROOT / "config"):
            self.assertEqual(cl.check_shadow(), [])


if __name__ == "__main__":
    unittest.main()
