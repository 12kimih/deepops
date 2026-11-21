"""container_service's env: each value reaches the container as given.

A value carried in options (--env=K=V) is split by systemd at spaces and its % and $
expand (specifiers, variables); the env key quotes each entry with systemd_exec_arg.
The quoting was checked against systemd 255 itself: a user unit printing its argv got
spaces, %, $, \\, quotes, ; and an empty value back unchanged."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import in_tree  # noqa: E402

filters = in_tree.load("plugins/filter/systemd.py")
ROOT = Path(__file__).resolve().parents[2]


class Quote(unittest.TestCase):
    def test_every_special_character_is_escaped(self):
        self.assertEqual(filters.systemd_exec_arg('%h $X \\ "'), '"%%h $$X \\\\ \\""')

    def test_a_line_break_is_refused(self):
        with self.assertRaises(Exception):
            filters.systemd_exec_arg("a\nb")

    def test_the_template_quotes_each_entry(self):
        template = (ROOT / "roles/container_service/templates/container.service.j2").read_text()
        self.assertIn("--env {{ (name ~ '=' ~ value) | systemd_exec_arg }}", template)


if __name__ == "__main__":
    unittest.main()
