"""clustertools.prom: the textfile format, escaping and the round trip."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from clustertools import prom  # noqa: E402


class Prom(unittest.TestCase):
    def test_series_escapes_label_values(self):
        self.assertEqual(prom.series("m", {"p": 'a"b\\c\nd'}, 1), 'm{p="a\\"b\\\\c\\nd"} 1')

    def test_render_is_sorted_and_parse_inverts_it(self):
        values = {prom.key("b", x="2"): 3, prom.key("a"): 1, prom.key("b", x='q"uote'): 4}
        text = prom.render(values)
        self.assertEqual(text.splitlines()[0], "a{} 1")
        self.assertEqual(prom.parse(text), {k: str(v) for k, v in values.items()})

    def test_parse_skips_comments(self):
        self.assertEqual(prom.parse("# HELP m x\n# TYPE m gauge\nm{} 2\n"), {prom.key("m"): "2"})


if __name__ == "__main__":
    unittest.main()
