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


class Repository(unittest.TestCase):
    def test_this_repository_is_clean(self):
        with mock.patch.dict(os.environ, {}):
            self.assertEqual(cl.check_dead() + cl.check_doc_paths(), [])


if __name__ == "__main__":
    unittest.main()
