"""plugins/filter/tmpfiles.py: how the host's own tmpfiles.d rules keep a directory."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "lib"))
import in_tree  # noqa: E402

tmpfiles_rule = in_tree.load("plugins/filter/tmpfiles.py").tmpfiles_rule

# `systemd-tmpfiles --cat-config` as Ubuntu 24.04 and RHEL 9 print it, cut down.
UBUNTU = """# /usr/lib/tmpfiles.d/tmp.conf
D /tmp 1777 root root 30d
#q /var/tmp 1777 root root 30d
# /usr/lib/tmpfiles.d/x11.conf
D! /tmp/.X11-unix 1777 root root 10d
"""
RHEL = """# /usr/lib/tmpfiles.d/tmp.conf
q /tmp 1777 root root 10d
q /var/tmp 1777 root root 30d
"""


class TmpfilesRule(unittest.TestCase):
    def test_emptied_at_boot_and_aged(self):
        self.assertEqual(tmpfiles_rule(UBUNTU, "/tmp"), {"type": "D", "age": "30d"})

    def test_kept_across_boots(self):
        self.assertEqual(tmpfiles_rule(RHEL, "/tmp"), {"type": "d", "age": "10d"})
        self.assertEqual(tmpfiles_rule(RHEL, "/var/tmp"), {"type": "d", "age": "30d"})

    def test_no_rule_keeps_everything(self):
        """A commented rule is none: Ubuntu never cleans /var/tmp."""
        self.assertEqual(tmpfiles_rule(UBUNTU, "/var/tmp"), {"type": "d", "age": ""})

    def test_whole_paths_and_modifiers(self):
        self.assertEqual(tmpfiles_rule(UBUNTU, "/tmp/.X11-unix"), {"type": "D", "age": "10d"})
        self.assertEqual(tmpfiles_rule("D /tmpx 1777 root root 1d\n", "/tmp"), {"type": "d", "age": ""})

    def test_the_first_rule_decides(self):
        """systemd reads /etc's file in place of /usr/lib's and prints it first."""
        text = "# /etc/tmpfiles.d/tmp.conf\nd /tmp 1777 root root -\n" + UBUNTU
        self.assertEqual(tmpfiles_rule(text, "/tmp"), {"type": "d", "age": ""})

    def test_a_tilde_age_is_the_age(self):
        self.assertEqual(
            tmpfiles_rule("d /var/tmp 1777 root root ~7d\n", "/var/tmp"), {"type": "d", "age": "7d"}
        )

    def test_rules_that_create_nothing_are_skipped(self):
        text = "x /tmp/systemd-private-*\ne /tmp - - - 5d\nD /tmp 1777 root root 30d\n"
        self.assertEqual(tmpfiles_rule(text, "/tmp"), {"type": "D", "age": "30d"})


if __name__ == "__main__":
    unittest.main()
