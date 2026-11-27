#!/usr/bin/env python3
"""The Korean glossary (terms_ko.yml): the word to use, its title form, and the variants
refused.

    terms.py [PATH ...]    print each line using a refused variant: this repository's
                           tracked files, or just PATHs
    terms.py --site        the same for the site config (DEEPOPS_CONFIG_DIR) as committed
                           at DEEPOPS_SITE_REV (its staged tree; default HEAD), and each
                           line naming a retired command (retired_names.py)

The glossary is the one list, and each repository applies it to its own files at its
own commit, so none waits on another's checkout: tests/slack-text/test_terms.py over
deepops, the site config's commit hook (scripts/deepops/config-pre-commit.sh) runs
--site over the config, and scripts/docs/site_facts.py exports the list (public) for the
user docs, whose own test applies it. A refused variant is a phrase, never a lone
common word, so a verb in its own sense passes.

Titles are nouns, sentences stay descriptive (owner, 2026-11-27): an entry with a title
form (GPU 유휴 잡 beside 할당받은 GPU를 쓰지 않는 잡) wants the title in every title
position and the sentence form everywhere else, and no title ends a sentence (titles:
endings) unless its file is exempt (titles: exempt, each with why). The title positions
(titles()): Markdown headings, bold-only lines and a list item's bold lead, table header
rows, a Slack guide's title, table and bookmark (slack/*.md front matter); in Python a
Slack header block, a modal title, a **bold** lead in a string, a form field's label, a
button, a table() header and cluster_tools.cards' header, part label and fact label; a Grafana dashboard's or panel's title; an alert's summary.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GLOSSARY = Path(__file__).with_name("terms_ko.yml")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402
import retired_names  # noqa: E402
from repo_files import tracked  # noqa: E402

# The files that must quote the variants: the glossary, its reader, its doc and its tests.
QUOTING = {
    "scripts/deepops/terms_ko.yml",
    "scripts/deepops/terms.py",
    "docs/deepops/terms.md",
    "tests/slack-text/test_terms.py",
}
FIELDS = ("use", "title", "not", "title_not", "why", "scope", "id", "code")
KOREAN = re.compile("[\uac00-\ud7a3]")


class Glossary(list):
    """The entries, and the title rules (endings, exempt) as .titles."""

    titles = {}


def load(path=GLOSSARY):
    """The entries [{use, title, not, title_not, why, scope, id, code}], each variant
    compiled once (`patterns`, and `title_patterns` for title_not: refused in a title
    only), and the title rules; an entry needs a word (use or title), something it
    refuses or a title form beside its word, and a reason."""
    data = yaml.safe_load(Path(path).read_text()) or {}
    entries = Glossary(data.get("terms") or [])
    for e in entries:
        word = e.get("use") or e.get("title")
        refuses = e.get("not") or e.get("title_not") or (e.get("use") and e.get("title"))
        if not (word and refuses and e.get("why")):
            raise ValueError(f"{path}: every entry needs a word, what it refuses, and why: {e!r}")
        e.setdefault("scope", [])
        e.setdefault("not", [])
        e.setdefault("title_not", [])
        e["patterns"] = [re.compile(p) for p in e["not"]]
        e["title_patterns"] = [re.compile(p) for p in e["title_not"]]
    rules = data.get("titles") or {}
    for x in rules.get("exempt") or []:
        if not (x.get("path") and x.get("why")):
            raise ValueError(f"{path}: every title exemption needs path and why: {x!r}")
    entries.titles = {"endings": rules.get("endings", ""), "exempt": rules.get("exempt") or []}
    entries.endings = re.compile(entries.titles["endings"]) if entries.titles["endings"] else None
    return entries


def public(entries):
    """The glossary as the user docs read it (site.json terms): no compiled patterns."""
    return [{k: e[k] for k in FIELDS if k in e} for e in entries]


def public_titles(entries):
    """The title rules as the user docs read them (site.json titles)."""
    return dict(entries.titles)


_FENCE = re.compile(r"\s*(```|~~~)")
_PY = (
    re.compile(r'"type": "header", "text": \{"type": "plain_text", "text": f?"([^"]+)"'),
    re.compile(r'"title": \{"type": "plain_text", "text": f?"([^"]+)"'),
    re.compile(r'\*\*([^*"\n]+)\*\*(?=\\n|"|  |$)'),
    re.compile(r'\bfield\(\s*"[^"]+",\s*f?"([^"]+)"'),
    re.compile(r'\bbutton\(\s*f?"([^"]+)"'),
    re.compile(r'\bcards\.(?:title|part)\(\s*f?"([^"]+)"'),  # cluster_tools.cards: a header, a part's label
    re.compile(r'^\s*\(f?"([^"]+)",\s*f?"[^"]*",\s*"(?:ok|warn|act|off|\?)?"'),  # a cards.facts label
)


def titles(rel, lines):
    """[(line number, title text)] of the title positions in LINES of the file REL."""
    out = []
    if rel.endswith(".md"):
        fence, front = False, lines[:1] == ["---"]
        end = lines.index("---", 1) if front and "---" in lines[1:] else -1
        for i, line in enumerate(lines):
            if 0 < i < end:
                m = re.match(r"(title|table|bookmark):\s*(.*)", line)
                if m:
                    value = m.group(2)
                    out.append((i + 1, value.split("|")[1].strip() if m.group(1) == "bookmark" else value))
                continue
            if _FENCE.match(line):
                fence = not fence
                continue
            if fence:
                continue
            m = (re.match(r"\s*#{1,6}\s+(.*)", line) or re.match(r"\s*(?:[-*]\s+)?\*\*([^*]+)\*\*\s*:?\s*$", line)
                 or re.match(r"\s*[-*]\s+\*\*([^*]+)\*\*\s*:", line))  # fmt: skip
            if m:
                out.append((i + 1, m.group(1)))
            elif (
                line.lstrip().startswith("|")
                and i + 1 < len(lines)
                and re.match(r"\s*\|[\s:|-]+\|\s*$", lines[i + 1])
            ):
                out += [(i + 1, c.strip()) for c in line.strip().strip("|").split("|")]
    elif rel.endswith(".py"):
        for i, line in enumerate(lines):
            for p in _PY:
                out += [(i + 1, m.group(1)) for m in p.finditer(line)]
            if re.search(r"\bcards\.(?:title|part)\(\s*$", line) and i + 1 < len(lines):
                m = re.match(r'\s*f?"([^"]+)"', lines[i + 1])  # the label on the next line
                if m:
                    out.append((i + 2, m.group(1)))
            for m in re.finditer(r"\btable\(\(([^)]*)\)", line):
                out += [(i + 1, a or b) for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", m.group(1))]
    elif rel.endswith(".json"):
        out += [
            (i + 1, m.group(1))
            for i, line in enumerate(lines)
            for m in re.finditer(r'"title": "([^"]*)"', line)
        ]
    elif rel.endswith((".yml", ".yaml")) and "rules" in rel:
        out += [
            (i + 1, m.group(1))
            for i, line in enumerate(lines)
            for m in [re.match(r"\s*summary:\s*(.*)", line)]
            if m
        ]
    return out


def _bare(title):
    """TITLE as read: no generated-block markers, code, links, a heading's attributes
    ({ #id }, which keep an old anchor), a closing parenthetical ("(중요)" is no ending)
    or closing punctuation."""
    text = re.sub(r"<!-- (?:BEGIN|END) GENERATED: \S+ -->", "", title)
    text = re.sub(r"`[^`]*`|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", text)
    text = re.sub(r"\s*\{[^}]*\}\s*$", "", text.strip())
    text = re.sub(r"\s*\([^()]*\)\s*$", "", text)
    return text.rstrip(".:!? ").strip()


def title_violations(entries, rel, lines):
    """What breaks the titles rule in LINES of REL: a sentence form (or its constant) in a
    title, a title form (or its constant) outside one, a title ending a sentence."""
    out, found = [], titles(rel, lines)
    exempt = any(rel.startswith(x["path"]) for x in entries.titles["exempt"])
    here = [e for e in entries if not e["scope"] or rel.startswith(tuple(e["scope"]))]
    formed = [e for e in here if e.get("title") and e.get("use") and e["title"] != e["use"]]
    by_line = {}
    for n, t in found:
        by_line.setdefault(n, []).append(t)
        for e in formed:
            code = e.get("code") or {}
            if e["use"] in t or (code.get("use") and "{" + code["use"] + "}" in t):
                out.append(f"{rel}:{n}: title {t!r} -> {e['title']}")
        for e in here:
            for p in e["title_patterns"]:
                m = p.search(_bare(t))
                if m:
                    out.append(f"{rel}:{n}: title {t!r}: {m.group(0)!r} -> {e['title']}")
        if entries.endings and not exempt and KOREAN.search(t) and entries.endings.search(_bare(t)):
            out.append(f"{rel}:{n}: title {t!r} ends a sentence -> a noun")
    for n, line in enumerate(lines, 1):
        rest = line
        for t in by_line.get(n, []):
            rest = rest.replace(t, "")
        for e in formed:
            code = e.get("code") or {}
            if code.get("title") and re.match(rf"\s*{code['title']}\s*=", line):
                continue  # the constant's one definition (the glossary names it)
            if e["title"] in rest or (code.get("title") and "{" + code["title"] + "}" in rest):
                out.append(f"{rel}:{n}: {e['title']!r} outside a title -> {e['use']}")
    return out


def term(entries, ident):
    """The word of the entry IDENT (the one copy code that sends it compares against)."""
    return next(e["use"] for e in entries if e.get("id") == ident)


def violations(entries, files, root):
    """'path:line: variant -> use' for every line of FILES (paths under ROOT) using a
    refused variant, a scoped entry only on its paths, and every title rule broken."""
    texts = []
    for path in files:
        rel = Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
        try:
            texts.append((rel, Path(path).read_text()))
        except (UnicodeDecodeError, IsADirectoryError):
            continue
    return check_texts(entries, texts)


def check_texts(entries, texts):
    """violations() over [(path, text)]."""
    out = []
    for rel, text in texts:
        if rel in QUOTING:
            continue
        lines = text.splitlines()
        rules = [e for e in entries if not e["scope"] or rel.startswith(tuple(e["scope"]))]
        for n, line in enumerate(lines, 1):
            for e in rules:
                for p in e["patterns"]:
                    m = p.search(line)
                    if m:
                        out.append(f"{rel}:{n}: {m.group(0)!r} -> {e.get('use') or e['title']}")
        out += title_violations(entries, rel, lines)
    return out


def committed(repo, rev="HEAD"):
    """[(path, text)] of every text file of REPO as committed at REV (a commit or a tree,
    such as a hook's staged tree): read from git, never the checkout."""

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout

    out = []
    for name in filter(None, git("ls-tree", "-r", "-z", "--name-only", rev).split(b"\0")):
        try:
            out.append((os.fsdecode(name), git("cat-file", "blob", f"{rev}:{os.fsdecode(name)}").decode()))
        except (UnicodeDecodeError, subprocess.CalledProcessError):
            continue  # binary, or a submodule
    return out


def scan():
    """Every violation in this repository's tracked files."""
    return violations(load(), tracked("."), ROOT)


def scan_site():
    """Every violation in the site config (DEEPOPS_CONFIG_DIR) at DEEPOPS_SITE_REV: the
    glossary's, and each mention of a retired command (deepops holds its own files to
    that in tests/cluster-tools/test_retired_names.py; the user docs from site.json)."""
    site = os.environ.get("DEEPOPS_CONFIG_DIR") or str(ROOT / "config")
    texts = committed(site, os.environ.get("DEEPOPS_SITE_REV") or "HEAD")
    retired = [
        f"{rel}:{n}: {old!r} -> {new}" for rel, text in texts for n, old, new in retired_names.mentions(text)
    ]
    return [f"config/{v}" for v in check_texts(load(), texts) + retired]


def main(argv=None):
    parser = cli.parser(__doc__)
    parser.add_argument("paths", nargs="*", help="files to check instead of this repository")
    parser.add_argument("--site", action="store_true", help="check the site config as committed")
    args = parser.parse_args(argv)
    if args.site:
        found = scan_site()
    elif args.paths:
        found = violations(load(), [Path(p) for p in args.paths], Path.cwd())
    else:
        found = scan()
    if found:
        print("\n".join(found))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
