"""Unit tests for clustertools.render (roles/cluster_tools_lib), no terminal needed."""

import io
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from clustertools import render  # noqa: E402

ANSI = re.compile(r"\033\[[0-9;]*m")


def plain(s):
    return ANSI.sub("", s)


class ColourTests(unittest.TestCase):
    def test_reference_points(self):
        self.assertEqual(render.x256(255, 255, 255), 231)
        self.assertEqual(render.x256(0, 0, 0), 16)
        self.assertEqual(render.x16(*render.rgb(render.PALETTES["mocha"]["green"])), 92)
        self.assertEqual(render.x16(*render.rgb(render.PALETTES["latte"]["red"])), 31)
        self.assertEqual(render.x16(*render.rgb(render.PALETTES["mocha"]["text"])), 37)

    def test_every_role_resolves_in_every_flavour(self):
        for f in render.FLAVOURS:
            for depth in (24, 256, 16):
                t = render.Theme(f, color=True, depth=depth)
                self.assertTrue(all(t.c[r].startswith("\033[") for r in render.ROLES))

    def test_no_colour_means_no_escape(self):
        t = render.Theme(color=False)
        self.assertEqual(t.paint("crit", "x"), "x")
        self.assertEqual(t.bar(1, 2, 4), "##--" if t.ascii else "██░░")

    def test_flavour_names(self):
        self.assertEqual(render.flavour_name("Frappé"), "frappe")
        self.assertIsNone(render.flavour_name("solarized"))


class WidthTests(unittest.TestCase):
    def test_wide_and_combining(self):
        self.assertEqual(render.width("ab"), 2)
        self.assertEqual(render.width("한글"), 4)
        self.assertEqual(render.width("é"), 1)

    def test_trunc_and_pad(self):
        self.assertEqual(render.trunc("abcdef", 4), "abc~")
        self.assertEqual(render.trunc("한글한글", 5), "한글~")
        self.assertEqual(render.trunc("abc", 4), "abc")
        self.assertEqual(render.pad("ab", 4, "r"), "  ab")


class BarAndLoadTests(unittest.TestCase):
    def setUp(self):
        self.t = render.Theme(color=False, ascii=True)

    def test_bar(self):
        self.assertEqual(self.t.bar(0, 10, 10), "-" * 10)
        self.assertEqual(self.t.bar(1, 1000, 10), "#" + "-" * 9)  # any use shows
        self.assertEqual(self.t.bar(20, 10, 4), "####")  # clamped
        self.assertEqual(self.t.bar(3, 0, 4), "    ")  # no such resource

    def test_load_thresholds(self):
        self.assertEqual(
            [self.t.load(u, 100) for u in (0, 10, 50, 80, 95)], ["faint", "ok", "warn", "hot", "crit"]
        )
        self.assertEqual(self.t.load(1, 0), "faint")

    def test_tone_ladder(self):
        self.assertEqual(
            [self.t.tone(s) for s in (0, 1, 2, 3, 9)], ["text", "sub", "muted", "faint", "faint"]
        )


class GridTests(unittest.TestCase):
    def test_alignment_follows_plain_text(self):
        for color in (False, True):
            t = render.Theme(color=color, ascii=True, cols=80)
            g = render.Grid(t, ["NAME", "N"], align=["l", "r"])
            g.row([("alpha", "text"), ("3", "ok")])
            g.row([("b", "crit"), ("12", "")], gutter=(">", "self"))
            lines = [plain(x) for x in g.render("T", [("2 rows", "sub")]).splitlines()]
            self.assertEqual(lines[1], "  NAME   N")
            self.assertEqual(lines[3], "  alpha  3")
            self.assertEqual(lines[4], "> b     12")

    def test_multi_piece_cells_and_total(self):
        t = render.Theme(color=True, ascii=True, cols=80)
        g = render.Grid(t, ["GPU"], align=["l"])
        g.row([[("3/8", "warn"), (" ", "")] + t.bar_pieces(3, 8, 4)])
        g.row([("12/16", "ok")])
        self.assertEqual(g.widths(), [8])
        self.assertEqual(g.total(), 10)
        lines = [plain(x) for x in g.render("T").splitlines()]
        self.assertEqual(lines[3], "  3/8 ##--")  # 1.5 of 4 rounds up
        self.assertEqual(lines[4], "  12/16")

    def test_heading_widens_a_narrow_table(self):
        t = render.Theme(color=False, ascii=True)
        g = render.Grid(t, ["A"])
        g.row([("x", "")])
        head = g.render("WHY", [("17 jobs waiting", "")]).splitlines()[0]
        self.assertIn("WHY   17 jobs waiting", head)

    def test_detect_respects_never_and_columns(self):
        t = render.detect(color_when="never", stream=io.StringIO(), env={"COLUMNS": "77", "LANG": "C.UTF-8"})
        self.assertFalse(t.color)
        self.assertEqual(t.cols, 77)
        self.assertFalse(t.ascii)
        self.assertTrue(render.detect(stream=io.StringIO(), env={"LANG": "C"}).ascii)


class PunctuationTests(unittest.TestCase):
    def test_no_typographic_punctuation_in_glyphs(self):
        for ascii in (False, True):
            glyphs = "".join(render.Theme(ascii=ascii).g.values())
            self.assertIsNone(re.search("[–—·…]", glyphs))


if __name__ == "__main__":
    unittest.main()
