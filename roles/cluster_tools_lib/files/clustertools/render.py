"""Colour, glyphs and tables for the cluster's commands, so they never disagree about
what a colour means.

Hue is spent only on meaning (a state, a load, what is yours). How important a plain
value is goes by brightness, on the tone ladder. Output carries no typographic
punctuation (dashes, middle dots, ellipses): separators are spacing and colour, and
placeholders are ASCII. Widths are measured in terminal columns, so a wide character
never breaks alignment. Python 3.9 or newer, standard library only.
"""

import os
import shutil
import sys
import unicodedata

# --- Palettes (https://catppuccin.com/palette) ---------------------------------------

PALETTES = {
    "latte": dict(
        rosewater="dc8a78",
        flamingo="dd7878",
        pink="ea76cb",
        mauve="8839ef",
        red="d20f39",
        maroon="e64553",
        peach="fe640b",
        yellow="df8e1d",
        green="40a02b",
        teal="179299",
        sky="04a5e5",
        sapphire="209fb5",
        blue="1e66f5",
        lavender="7287fd",
        text="4c4f69",
        subtext1="5c5f77",
        subtext0="6c6f85",
        overlay2="7c7f93",
        overlay1="8c8fa1",
        overlay0="9ca0b0",
        surface2="acb0be",
        surface1="bcc0cc",
        surface0="ccd0da",
    ),
    "frappe": dict(
        rosewater="f2d5cf",
        flamingo="eebebe",
        pink="f4b8e4",
        mauve="ca9ee6",
        red="e78284",
        maroon="ea999c",
        peach="ef9f76",
        yellow="e5c890",
        green="a6d189",
        teal="81c8be",
        sky="99d1db",
        sapphire="85c1dc",
        blue="8caaee",
        lavender="babbf1",
        text="c6d0f5",
        subtext1="b5bfe2",
        subtext0="a5adce",
        overlay2="949cbb",
        overlay1="838ba7",
        overlay0="737994",
        surface2="626880",
        surface1="51576d",
        surface0="414559",
    ),
    "macchiato": dict(
        rosewater="f4dbd6",
        flamingo="f0c6c6",
        pink="f5bde6",
        mauve="c6a0f6",
        red="ed8796",
        maroon="ee99a0",
        peach="f5a97f",
        yellow="eed49f",
        green="a6da95",
        teal="8bd5ca",
        sky="91d7e3",
        sapphire="7dc4e4",
        blue="8aadf4",
        lavender="b7bdf8",
        text="cad3f5",
        subtext1="b8c0e0",
        subtext0="a5adcb",
        overlay2="939ab7",
        overlay1="8087a2",
        overlay0="6e738d",
        surface2="5b6078",
        surface1="494d64",
        surface0="363a4f",
    ),
    "mocha": dict(
        rosewater="f5e0dc",
        flamingo="f2cdcd",
        pink="f5c2e7",
        mauve="cba6f7",
        red="f38ba8",
        maroon="eba0ac",
        peach="fab387",
        yellow="f9e2af",
        green="a6e3a1",
        teal="94e2d5",
        sky="89dceb",
        sapphire="74c7ec",
        blue="89b4fa",
        lavender="b4befe",
        text="cdd6f4",
        subtext1="bac2de",
        subtext0="a6adc8",
        overlay2="9399b2",
        overlay1="7f849c",
        overlay0="6c7086",
        surface2="585b70",
        surface1="45475a",
        surface0="313244",
    ),
}
FLAVOURS = ("mocha", "macchiato", "frappe", "latte")
DEFAULT_FLAVOUR = "mocha"

# Semantic roles -> palette entries. Callers name the ROLE, never the colour: "this is
# a heading" survives a flavour change, "this is mauve" does not.
ROLES = {
    "text": "text",  # primary values
    "sub": "subtext0",  # secondary values
    "head": "subtext1",  # column headers (bold)
    "muted": "overlay1",  # de-emphasised (totals, pending rows)
    "faint": "overlay0",  # idle, not applicable
    "rule": "surface1",  # rules, bar troughs
    "acc": "mauve",  # section headings
    "part": "blue",  # pool headings, command names
    "node": "subtext1",  # node names
    "self": "lavender",  # yours: your jobs, the QoS you may use
    "ok": "green",  # running, lightly loaded
    "warn": "yellow",  # pending, half loaded
    "hot": "peach",  # suspended, heavily loaded
    "crit": "red",  # failed, saturated, down
    "info": "sky",  # completing, paths and URLs
    "done": "teal",  # completed
}
TONES = ("text", "sub", "muted", "faint")  # the brightness ladder for plain text


def flavour_name(name):
    """A known flavour for NAME (any case, frappé spelled either way), else None."""
    name = (name or "").lower().replace("é", "e")
    return name if name in PALETTES else None


def rgb(hexcode):
    return tuple(int(hexcode[i : i + 2], 16) for i in (0, 2, 4))


def x256(r, g, b):
    """Nearest xterm-256 index: the better of the 6x6x6 cube and the grey ramp."""
    levels = (0, 95, 135, 175, 215, 255)
    idx = [min(range(6), key=lambda i: abs(c - levels[i])) for c in (r, g, b)]
    cube = [levels[i] for i in idx]
    dc = sum((c - v) ** 2 for c, v in zip((r, g, b), cube))
    gi = max(0, min(23, ((r + g + b) // 3 - 8 + 5) // 10))
    gv = 8 + 10 * gi
    dg = sum((c - gv) ** 2 for c in (r, g, b))
    return 232 + gi if dg < dc else 16 + 36 * idx[0] + 6 * idx[1] + idx[2]


def x16(r, g, b):
    """SGR code of the ANSI colour nearest in HUE. Distance in RGB would send every
    pastel to white; mapping by hue keeps green green on a 16-colour terminal."""
    mx, mn = max(r, g, b), min(r, g, b)
    if 100 * (mx - mn) < 28 * mx:  # unsaturated: a grey
        return 37 if mx + mn >= 340 else 90
    if mx == r:
        h = (60 * (g - b) // (mx - mn) + 360) % 360
    elif mx == g:
        h = 60 * (b - r) // (mx - mn) + 120
    else:
        h = 60 * (r - g) // (mx - mn) + 240
    if h < 30 or h >= 330:
        n = 1  # red
    elif h < 90:
        n = 3  # yellow
    elif h < 150:
        n = 2  # green
    elif h < 210:
        n = 6  # cyan
    elif h < 255:
        n = 4  # blue (mauve, ~267, reads as violet)
    else:
        n = 5  # magenta
    return (90 if mx + mn >= 256 else 30) + n


def sgr(hexcode, depth):
    r, g, b = rgb(hexcode)
    if depth == 24:
        return f"\033[38;2;{r};{g};{b}m"
    if depth == 256:
        return f"\033[38;5;{x256(r, g, b)}m"
    return f"\033[{x16(r, g, b)}m"


# --- Width ----------------------------------------------------------------------------


def width(text):
    """Terminal columns TEXT takes: wide East Asian characters count two, combining
    marks none."""
    w = 0
    for c in text:
        if unicodedata.combining(c):
            continue
        w += 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
    return w


def trunc(text, cols, mark="~"):
    """TEXT cut to COLS columns, the last one a truncation mark when anything was cut."""
    if cols <= 0 or width(text) <= cols:
        return text
    out, used = "", 0
    for c in text:
        cw = width(c)
        if used + cw > cols - width(mark):
            break
        out, used = out + c, used + cw
    return out + mark


def duration(seconds, short=True):
    """A duration as Slurm writes one. SHORT is squeue's (M:SS, H:MM:SS, D-HH:MM:SS),
    for elapsed times; otherwise always [D-]HH:MM:SS, as limits are written."""
    days, rest = divmod(int(seconds), 86400)
    h, rest = divmod(rest, 3600)
    m, s = divmod(rest, 60)
    if days:
        return f"{days}-{h:02d}:{m:02d}:{s:02d}"
    if not short:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def pad(text, cols, align="l"):
    gap = " " * max(0, cols - width(text))
    return gap + text if align == "r" else text + gap


# --- Theme ----------------------------------------------------------------------------


class Theme:
    """Colours and glyphs for one output stream. Role colours are SGR prefixes, empty
    without colour; paint() wraps a text in one."""

    def __init__(self, flavour=DEFAULT_FLAVOUR, color=True, depth=24, ascii=False, cols=100):
        self.flavour, self.color, self.depth, self.ascii, self.cols = flavour, color, depth, ascii, cols
        palette = PALETTES[flavour]
        self.c = {role: sgr(palette[entry], depth) if color else "" for role, entry in ROLES.items()}
        self.bold, self.reset = ("\033[1m", "\033[0m") if color else ("", "")
        self.c["head"] += self.bold
        if ascii:
            self.g = dict(
                sec="|",
                bar_f="#",
                bar_e="-",
                rule="-",
                self=">",
                tee="|- ",
                end="`- ",
                pipe="|  ",
                gap="   ",
                inf="inf",
                arrow="->",
                check="+",
                cross="x",
            )
        else:
            self.g = dict(
                sec="▎",
                bar_f="█",
                bar_e="░",
                rule="─",
                self="▌",
                tee="├─ ",
                end="└─ ",
                pipe="│  ",
                gap="   ",
                inf="∞",
                arrow="→",
                check="✓",
                cross="✕",
            )
        self.g.update(ell="~", dot="-")  # truncation mark, "no value" placeholder

    def paint(self, style, text):
        """TEXT in STYLE: a role name, a raw SGR prefix, or "" for none."""
        prefix = self.c.get(style, style)
        return f"{prefix}{text}{self.reset}" if prefix and text else text

    def tone(self, step):
        """The role on the brightness ladder: 0 text, 1 sub, 2 muted, 3 faint (and past)."""
        return TONES[min(max(step, 0), len(TONES) - 1)]

    def load(self, used, total):
        """The role for how full something is; the same thresholds in every command."""
        if total <= 0:
            return "faint"
        pct = 100 * used // total
        if pct >= 95:
            return "crit"
        if pct >= 80:
            return "hot"
        if pct >= 50:
            return "warn"
        return "ok" if used > 0 else "faint"

    def bar_pieces(self, used, total, cols, style=None):
        """A utilisation bar of COLS columns as (text, style) pieces, for a Grid cell:
        coloured by load unless STYLE fixes the fill (a share rather than a load). No
        such resource (total 0) is blanks: having none here differs from having none
        left, which an empty trough would claim."""
        if cols <= 0:
            return []
        if total <= 0:
            return [(" " * cols, "")]
        filled = min(cols, (used * cols + total // 2) // total)
        if filled == 0 and used > 0:
            filled = 1  # any use at all must be visible
        return [
            (self.g["bar_f"] * filled, style or self.load(used, total)),
            (self.g["bar_e"] * (cols - filled), "rule"),
        ]

    def bar(self, used, total, cols, style=None):
        """The same bar, painted."""
        return "".join(self.paint(s, x) for x, s in self.bar_pieces(used, total, cols, style))

    def section(self, title, cols, tally=()):
        """A heading with a right-aligned tally of (text, style) items, set apart by
        space and colour rather than a separator glyph."""
        plain = "   ".join(t for t, _ in tally)
        coloured = "   ".join(self.paint(s, t) for t, s in tally)
        gap = max(1, cols - 2 - width(self.g["sec"]) - width(title) - width(plain))
        return f"  {self.paint(self.c['acc'] + self.bold, self.g['sec'] + title)}{' ' * gap}{coloured}"

    def rule(self, cols):
        return "  " + self.paint("rule", self.g["rule"] * max(0, cols - 2))


def _terminfo_depth(term, fd):
    """24 or 16 when terminfo says so for TERM, else None. No curses module (a minimal
    Python) or no terminfo entry for TERM is None too; anything else is a bug and raised."""
    try:
        import curses
    except ImportError:
        return None
    try:
        curses.setupterm(term, fd)
    except curses.error:
        return None
    if curses.tigetflag("RGB") > 0 or curses.tigetflag("Tc") > 0:
        return 24
    return 16 if 0 < curses.tigetnum("colors") < 256 else None


def detect(flavour=None, color_when="auto", ascii=False, wide=False, stream=None, env=None):
    """The Theme for STREAM (stdout by default) from the command line and environment.

    Colour: always, never or auto (a terminal, $NO_COLOR unset, $TERM not dumb). Depth:
    24-bit where $COLORTERM or terminfo says so, else 256 unless terminfo reports fewer.
    Glyphs fall back to ASCII without a UTF-8 locale. Width: $COLUMNS, else the
    terminal, else 100; --wide lifts it so every column shows at full width."""
    env = os.environ if env is None else env
    stream = stream or sys.stdout
    tty = stream.isatty()
    if color_when in ("never", "no", "off"):
        color = False
    elif color_when in ("always", "yes", "force"):
        color = True
    else:
        color = tty and not env.get("NO_COLOR") and env.get("TERM", "dumb") != "dumb"

    depth = 24 if env.get("COLORTERM") in ("truecolor", "24bit") else 256
    if color and depth != 24:
        depth = _terminfo_depth(env.get("TERM"), stream.fileno() if tty else -1) or depth

    ctype = env.get("LC_ALL") or env.get("LC_CTYPE") or env.get("LANG") or ""
    ascii = ascii or "utf" not in ctype.lower()

    try:
        cols = int(env.get("COLUMNS", "0"))
    except ValueError:
        cols = 0
    if cols <= 0:
        cols = shutil.get_terminal_size((100, 24)).columns if tty else 100
    if wide:
        cols = 1000
    return Theme(flavour or DEFAULT_FLAVOUR, color, depth, ascii, cols)


# --- Grid: a table of styled cells ----------------------------------------------------


class Grid:
    """A table whose widths follow the plain text, so colour never disturbs alignment.

    row(cells, gutter, prefix, tail): a cell is a (text, style) pair, or a list of them
    for a cell built of differently styled pieces (a figure and its bar); GUTTER is a
    one-column mark ("" for none, or a (text, style) pair); PREFIX tree glyphs before
    the first cell; TAIL a rendered string of tail_width columns after the last.
    rule() and divider() separate groups of rows. Free text has no place between rows:
    it breaks the tree lines and the columns a reader follows. Explanations belong in a
    column, a heading's tally, or the command's --help.
    Only single-piece cells are capped by maxw.
    """

    def __init__(self, theme, headers, align=None, maxw=None, tail_header="", tail_width=0, header_rule=True):
        self.t, self.headers = theme, list(headers)
        self.align = list(align or [])
        self.maxw = list(maxw or [])
        self.tail_header, self.tail_width = tail_header, tail_width
        self.header_rule = header_rule
        self.rows = []

    def row(self, cells, gutter="", prefix="", tail=""):
        self.rows.append(("row", list(cells), gutter, prefix, tail))

    def rule(self):
        self.rows.append(("rule",))

    def divider(self, label, style):
        """A rule with a label in it, heading a group of rows: ── LABEL ─────."""
        self.rows.append(("divider", label, style))

    def _pieces(self, cell, col):
        pieces = list(cell) if isinstance(cell, list) else [cell]
        pieces = [(str(text), style) for text, style in pieces]
        cap = self.maxw[col] if col < len(self.maxw) else 0
        if cap and len(pieces) == 1:
            pieces = [(trunc(pieces[0][0], cap, self.t.g["ell"]), pieces[0][1])]
        return pieces

    def _align(self, col):
        return self.align[col] if col < len(self.align) else "l"

    def _cells(self, r):
        n = len(self.headers)
        return [self._pieces(r[1][c] if c < len(r[1]) else ("", ""), c) for c in range(n)]

    def widths(self):
        """The width of each column at the widest cell it holds."""
        widths = [width(h) for h in self.headers]
        for r in self.rows:
            if r[0] == "row":
                for c, pieces in enumerate(self._cells(r)):
                    w = sum(width(t) for t, _ in pieces) + (width(r[3]) if c == 0 else 0)
                    widths[c] = max(widths[c], w)
        return widths

    def total(self):
        """Columns the table takes, from the margin to the end of its tail."""
        total = 2 + (len(self.headers) - 1) + sum(self.widths())
        return total + (1 + self.tail_width if self.tail_width else 0)

    def render(self, title, tally=()):
        t, widths = self.t, self.widths()
        # A narrow table still leaves its heading's title and tally apart.
        plain = "   ".join(x for x, _ in tally)
        total = max(self.total(), 2 + width(t.g["sec"]) + width(title) + 3 + width(plain))

        out = [t.section(title, total, tally)]
        head = "  " + " ".join(pad(h, widths[c], self._align(c)) for c, h in enumerate(self.headers))
        if self.tail_header:
            head += " " + self.tail_header
        out.append(t.paint("head", head.rstrip()))
        if self.header_rule:
            out.append(t.rule(total))
        for r in self.rows:
            if r[0] == "rule":
                out.append(t.rule(total))
                continue
            if r[0] == "divider":
                g = t.g["rule"]
                tail = g * max(0, total - width(r[1]) - 6)
                out.append(f"  {t.paint('rule', g * 2)} {t.paint(r[2], r[1])} {t.paint('rule', tail)}")
                continue
            gutter, prefix, tail = r[2], r[3], r[4]
            if isinstance(gutter, tuple):
                gutter = t.paint(gutter[1], gutter[0])
            line = (gutter or " ") + " "
            for c, pieces in enumerate(self._cells(r)):
                room = widths[c] - (width(prefix) if c == 0 else 0)
                if c == 0:
                    line += t.paint("rule", prefix)
                gap = " " * max(0, room - sum(width(x) for x, _ in pieces))
                body = "".join(t.paint(style, x) for x, style in pieces)
                line += (gap + body if self._align(c) == "r" else body + gap) + " "
            out.append(line + tail if tail else line.rstrip())
        return "\n".join(out)
