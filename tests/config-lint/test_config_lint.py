"""config_lint.py: each check, and --fix."""

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


def owned(**values):
    return {k: cl.settings.Owner([f"roles/r/defaults/main.yml#{k}"], v) for k, v in values.items()}


class Configs(unittest.TestCase):
    """A temporary config.example and site config, and fixed role defaults."""

    OWNERS = owned(x=1, y=[1, 2], z="a")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.example, self.site = base / "example", base / "site"
        for d in (self.example / "group_vars", self.site / "group_vars"):
            d.mkdir(parents=True)
        (self.site / "inventory").write_text("[g]\nh1\n")
        for patch in (
            mock.patch.multiple(cl, EXAMPLE=self.example, CONFIG=self.site),
            mock.patch.object(cl.settings, "owners", return_value=self.OWNERS),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def write(self, example=None, site=None):
        if example is not None:
            (self.example / "group_vars/all.yml").write_text(example)
        if site is not None:
            (self.site / "group_vars/all.yml").write_text(site)


class Commented(Configs):
    def test_defaults_and_examples_pass(self):
        self.write("#x: 1\n#y:\n#  - 1\n#  - 2\n#z: b  # example\n#free: 3  # example\n")
        self.assertEqual(cl.check_commented(), [])

    def test_a_stale_default_is_reported_and_fixed(self):
        self.write("# What x is.\n#x: 2\n#y: [1]\n")
        found = cl.check_commented()
        self.assertEqual(len(found), 2, found)
        self.assertIn("shows 2, its default", found[0])
        fix = cl.Fixes()
        cl.check_commented(fix)
        fix.apply()
        self.assertEqual(
            (self.example / "group_vars/all.yml").read_text(), "# What x is.\n#x: 1\n#y:\n#- 1\n#- 2\n"
        )
        self.assertEqual(cl.check_commented(), [])

    def test_an_unmarked_value_without_a_default_and_a_marked_default(self):
        found = (self.write("#free: 3\n#x: 1  # example\n"), cl.check_commented())[1]
        self.assertEqual(len(found), 2, found)
        self.assertIn("mark it # example", found[0])
        self.assertIn("drop the mark", found[1])


class Retired(Configs):
    def test_a_config_setting_a_retired_name_is_reported(self):
        self.write("#x: 1\n#old_example: 1\n", "x: 2\nold_site: 3\n")
        with mock.patch.object(cl, "retired", return_value={"old_site": "x", "old_example": "x"}):
            found = cl.check_retired()
        self.assertEqual(found, ["retired: config/group_vars/all.yml:2: old_site is retired: set x instead"])


class Redundant(Configs):
    def test_a_restated_default_is_reported_and_fixed(self):
        self.write("x: 1\nz: b\n", "# site: kept on purpose\nx: 1\ny: [1, 2]\nz: b\n")
        found = cl.check_redundant()
        self.assertEqual(found[0], "redundant: config.example/group_vars/all.yml:1: x: equals what it would be "
                                   "without it; comment it out")  # fmt: skip
        self.assertEqual(found[1:], ["redundant: config/group_vars/all.yml:3: equals what it would be without "
                                     "it; delete it, or say why in # site:"])  # fmt: skip
        fix = cl.Fixes()
        cl.check_redundant(fix)
        fix.apply()
        self.assertEqual((self.example / "group_vars/all.yml").read_text(), "#x: 1\nz: b\n")
        self.assertEqual(
            (self.site / "group_vars/all.yml").read_text(), "# site: kept on purpose\nx: 1\nz: b\n"
        )
        self.assertEqual(cl.check_redundant(), [])

    def test_under_the_commit_hook_the_site_is_read_as_committed_and_never_fixed(self):
        # pre-commit stashes this repository's unstaged edits, never the site's: a
        # half-done site edit must not fail another commit, nor a committed shadow hide.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(self.site)]
        self.write("", "x: 2\n")
        import subprocess

        subprocess.run(git[:-2] + ["init", "-q", str(self.site)], check=True, env=env)
        subprocess.run(git + ["add", "-A"], check=True, env=env)
        subprocess.run(git + ["commit", "-qm", "c"], check=True, env=env)
        self.write(site="x: 1\n")
        (self.site / "group_vars/all").mkdir()
        by_hand = {k: v for k, v in os.environ.items() if k != "PRE_COMMIT"}
        with mock.patch.dict(os.environ, by_hand, clear=True):
            self.assertTrue(cl.check_redundant())
            self.assertTrue(cl.check_shadow())
        with mock.patch.dict(os.environ, {"PRE_COMMIT": "1"}):
            self.assertEqual(cl.check_redundant(), [])
            self.assertEqual(cl.check_shadow(), [])
            fix = cl.Fixes()
            cl.check_redundant(fix)
            self.assertEqual(fix.edits, {})


class Repository(unittest.TestCase):
    def test_this_repository_is_clean(self):
        with mock.patch.dict(os.environ, {}):
            self.assertEqual(
                cl.check_commented() + cl.check_redundant() + cl.check_dead() + cl.check_doc_paths()
                + cl.check_doc_vars(), []
            )  # fmt: skip


class DocVars(unittest.TestCase):
    """A page naming a renamed variable sends the reader to a setting nothing reads
    (AGENTS.md once said nvidia_driver_ubuntu_use_open_kernel_modules: false)."""

    def problems(self, text):
        with tempfile.TemporaryDirectory() as d:
            doc = Path(d) / "page.md"
            doc.write_text(text)
            with (
                mock.patch.object(cl, "ROOT", Path(d)),
                mock.patch.object(cl, "tracked", return_value=[doc]),
                mock.patch.object(cl, "read_words", return_value={"slurm_install_nhc"}),
            ):
                return cl.check_doc_vars()

    def test_a_setting_and_a_table_name_are_checked(self):
        found = self.problems(
            "set `old_flag_name: false` here\n| `gone_setting` | yes |\n| `slurm_install_nhc` | `x_y: 1` |\n"
        )
        self.assertEqual(
            found,
            [
                "doc-vars: page.md:1: nothing reads old_flag_name",
                "doc-vars: page.md:2: nothing reads gone_setting",
            ],
        )

    def test_read_settings_and_prose_words_pass(self):
        self.assertEqual(self.problems("set `slurm_install_nhc: true`; the `no_wdelay` option\n"), [])


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
