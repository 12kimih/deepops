"""scripts/deepops/galaxy.py's swap: the new set replaces the live one, a live symlink is
replaced and never followed, leftovers of an older run go, and failures are not hidden."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("galaxy", ROOT / "scripts/deepops/galaxy.py")
galaxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(galaxy)


def tree(path: Path, name: str) -> Path:
    path.mkdir(parents=True)
    (path / name).write_text(name)
    return path


class Swap(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)  # after a test's own cleanups (they run last first)
        self.base = Path(tmp.name)

    def check_swap(self, atomic: bool):
        live = tree(self.base / "collections", "old")
        new = tree(self.base / ".collections.new", "new")
        with mock.patch.object(galaxy, "exchange", wraps=galaxy.exchange if atomic else lambda a, b: False):
            galaxy.swap(live, new)
        self.assertEqual(sorted(p.name for p in live.iterdir()), ["new"])
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ["collections"])

    def test_the_new_set_replaces_the_live_one_atomically(self):
        self.check_swap(atomic=True)

    def test_the_new_set_replaces_the_live_one_with_two_renames(self):
        self.check_swap(atomic=False)

    def test_a_live_symlink_is_replaced_and_its_target_kept(self):
        shared = tree(self.base / "shared" / "collections", "shared")
        live = self.base / "wt" / "collections"
        live.parent.mkdir()
        live.symlink_to(shared)
        for atomic in (True, False):
            with self.subTest(atomic=atomic):
                if live.is_dir() and not live.is_symlink():
                    galaxy.remove(live)
                    live.symlink_to(shared)
                new = tree(self.base / "wt" / ".collections.new", "new")
                with mock.patch.object(
                    galaxy, "exchange", wraps=galaxy.exchange if atomic else lambda a, b: False
                ):
                    galaxy.swap(live, new)
                self.assertFalse(live.is_symlink())
                self.assertEqual([p.name for p in live.iterdir()], ["new"])
                self.assertEqual([p.name for p in shared.iterdir()], ["shared"], "the target stays")
                self.assertEqual(sorted(p.name for p in live.parent.iterdir()), ["collections"])

    def test_remove_unlinks_a_symlink_and_never_its_target(self):
        target = tree(self.base / "target", "keep")
        link = self.base / ".collections.old"
        link.symlink_to(target)
        galaxy.remove(link)
        self.assertFalse(link.is_symlink())
        self.assertTrue((target / "keep").exists())

    def test_remove_does_not_hide_a_failure(self):
        locked = tree(self.base / "locked" / "sub", "f").parent
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)
        with self.assertRaises(PermissionError):
            galaxy.remove(locked / "sub")


if __name__ == "__main__":
    unittest.main()
