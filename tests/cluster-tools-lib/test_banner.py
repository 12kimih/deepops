"""Unit tests for clustertools.banner (roles/cluster_tools_lib): the login banner."""

import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from clustertools import banner, render  # noqa: E402

ANSI = re.compile(r"\033\[[0-9;]*m")
COMMANDS = [{"name": "sq", "text": "queue and node usage"}, {"name": "qos", "text": "your QoS"}]
LINKS = [{"name": "guide", "text": "https://example.org/docs/"}]


def shown(script):
    """What a login prints: the fragment run by sh."""
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True, check=True).stdout


class BannerTests(unittest.TestCase):
    def test_layout(self):
        out = shown(banner.script("my cluster", COMMANDS, LINKS, color=False)).splitlines()
        self.assertEqual(out[1], "  ▎my cluster")
        self.assertEqual(out[3], "   sq     queue and node usage")
        self.assertEqual(out[6], "   guide  https://example.org/docs/")
        self.assertEqual(len(out[2].strip()), 3 + len("guide") + len("https://example.org/docs/"))

    def test_text_is_printed_as_written_never_run(self):
        title = "it's $HOME and `id` and $(whoami)"
        out = shown(banner.script(title, COMMANDS, color=False))
        self.assertIn(title, out)

    def test_colours_come_from_the_shared_palette(self):
        script = banner.script("t", COMMANDS, LINKS, flavour="latte")
        for role in ("acc", "part", "info", "sub", "rule"):
            self.assertIn(render.Theme("latte").c[role], script)
        self.assertEqual(ANSI.sub("", shown(script)), shown(banner.script("t", COMMANDS, LINKS, color=False)))

    def test_unknown_flavour_fails_loudly(self):
        with self.assertRaisesRegex(ValueError, "unknown flavour"):
            banner.script("t", COMMANDS, flavour="solarized")

    def test_a_row_named_like_the_terminator_cannot_end_the_text(self):
        rows = [{"name": "BANNER", "text": "x"}, {"name": "y", "text": "BANNER"}]
        out = shown(banner.script("t", rows, LINKS, color=False))
        self.assertIn("https://example.org/docs/", out)


if __name__ == "__main__":
    unittest.main()
