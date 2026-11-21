"""roles/source_build/library/staged_install.py on a scratch root: an install lists what
it put in place, a new version removes only what the old one alone had, and a removal
takes everything listed and the directories it made, never one that was there."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402

mod = in_tree.load("roles/source_build/library/staged_install.py")


class StagedInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.host = self.tmp / "host"
        (self.host / "usr/local/bin").mkdir(parents=True)
        (self.host / "usr/local/bin/someone-elses").write_text("x")
        self.manifest = str(self.tmp / "m.json")

    def stage(self, version, files):
        stage = self.tmp / f"stage-{version}"
        for rel, text in files.items():
            path = stage / rel.lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return str(stage)

    def install(self, stage):
        files, dirs = mod.staged(stage)
        old_files, old_dirs = mod.read_manifest(self.manifest)
        made = mod.copy_into_place(str(self.host), stage, files, dirs)
        mod.remove_files(str(self.host), old_files - files)
        mod.remove_empty_dirs(str(self.host), old_dirs - dirs)
        with open(self.manifest, "w") as f:
            json.dump({"files": sorted(files), "dirs": sorted((old_dirs & dirs) | made)}, f)

    def test_a_new_version_leaves_nothing_of_the_old_one(self):
        self.install(self.stage("1", {"/usr/local/bin/tool": "1", "/usr/local/share/tool/old.dat": "o"}))
        self.install(self.stage("2", {"/usr/local/bin/tool": "2", "/usr/local/lib/libtool2.so": "l"}))
        self.assertEqual((self.host / "usr/local/bin/tool").read_text(), "2")
        self.assertFalse((self.host / "usr/local/share/tool").exists())  # made by v1, emptied
        self.assertTrue((self.host / "usr/local/lib/libtool2.so").exists())
        self.assertTrue((self.host / "usr/local/bin/someone-elses").exists())

    def test_removal_takes_what_it_listed_and_only_the_directories_it_made(self):
        self.install(self.stage("1", {"/usr/local/bin/tool": "1", "/opt/apps/tool/1/x": "x"}))
        files, dirs = mod.read_manifest(self.manifest)
        mod.remove_files(str(self.host), files)
        mod.remove_empty_dirs(str(self.host), dirs)
        self.assertFalse((self.host / "opt").exists())
        self.assertTrue((self.host / "usr/local/bin").is_dir())  # there before
        self.assertTrue((self.host / "usr/local/bin/someone-elses").exists())

    def test_links_are_copied_as_links(self):
        stage = self.stage("1", {"/opt/apps/lmod/9/x": "x"})
        os.symlink("9", Path(stage) / "opt/apps/lmod/lmod")
        self.install(stage)
        self.assertEqual(os.readlink(self.host / "opt/apps/lmod/lmod"), "9")


if __name__ == "__main__":
    unittest.main()
