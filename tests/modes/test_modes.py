"""mode_exceeds (plugins/filter/modes.py)."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("modes", ROOT / "plugins" / "filter" / "modes.py")
modes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(modes)


class ModeExceeds(unittest.TestCase):
    def test_wider_modes_exceed(self):
        for mode in ("0755", "0751", "0770", "0777", "2750"):
            self.assertTrue(modes.mode_exceeds(mode, "0750"), mode)

    def test_equal_and_stricter_do_not(self):
        for mode in ("0750", "0700", "0710", "0500"):
            self.assertFalse(modes.mode_exceeds(mode, "0750"), mode)


if __name__ == "__main__":
    unittest.main()
