"""plugins/filter/apt.py, and that every apt pin a role writes goes through it."""

import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

spec = importlib.util.spec_from_file_location("apt_filter", ROOT / "plugins" / "filter" / "apt.py")
apt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apt)


class PinPackages(unittest.TestCase):
    def test_every_pattern_matches_every_architecture(self):
        self.assertEqual(apt.apt_pin_packages("slurm-smd slurm-smd-*"), "slurm-smd:any slurm-smd-*:any")
        self.assertEqual(apt.apt_pin_packages(["*nvidia*"]), "*nvidia*:any")
        self.assertEqual(apt.apt_pin_packages("*"), "*:any")

    def test_a_named_architecture_is_kept(self):
        self.assertEqual(apt.apt_pin_packages("foo:i386 bar"), "foo:i386 bar:any")

    def test_a_regex_or_nothing_is_refused(self):
        for patterns in ("/nvidia/", "", []):
            with self.subTest(patterns=patterns), self.assertRaises(apt.AnsibleFilterError):
                apt.apt_pin_packages(patterns)


class Roles(unittest.TestCase):
    def test_every_pin_written_names_its_packages_through_the_filter(self):
        # A Package: line of an apt preferences file a task writes (not a debian/control
        # paragraph, which declares a package it builds).
        found = []
        for path in tracked("roles", "playbooks"):
            if path.suffix not in (".yml", ".yaml", ".j2") or "molecule" in path.parts:
                continue
            text = path.read_text(errors="ignore")
            for match in re.finditer(r"(?m)^\s*Package: (.+)$", text):
                after = text[match.end() : match.end() + 200]
                if "Pin:" in after and "apt_pin_packages" not in match.group(1):
                    found.append(f"{path.relative_to(ROOT)}: {match.group(0).strip()}")
        self.assertEqual(found, [], "write the Package line as {{ ... | apt_pin_packages }}")


if __name__ == "__main__":
    unittest.main()
