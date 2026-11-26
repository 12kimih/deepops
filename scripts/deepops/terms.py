#!/usr/bin/env python3
"""The Korean glossary (terms_ko.yml): the word to use and the variants refused.

    terms.py [PATH ...]    print each line using a refused variant (the tracked files of
                           this repository and of the site config when no PATH is given)

The glossary is the one list; tests/slack-text/test_terms.py runs this over deepops and
the site config, and scripts/docs/site_facts.py exports it (public) for the user docs,
whose own test applies the same list. A refused variant is a phrase, never a lone
common word, so a verb in its own sense passes.
"""

import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GLOSSARY = Path(__file__).with_name("terms_ko.yml")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402
from repo_files import tracked  # noqa: E402

# The files that must quote the variants: the glossary and its tests.
QUOTING = {"scripts/deepops/terms_ko.yml", "tests/slack-text/test_terms.py"}


def load(path=GLOSSARY):
    """[{use, not, why, scope, id}] with each variant compiled once (as `patterns`);
    an entry without a word, a variant or a reason is refused."""
    entries = yaml.safe_load(Path(path).read_text()) or []
    for e in entries:
        if not (e.get("use") and e.get("not") and e.get("why")):
            raise ValueError(f"{path}: every entry needs use, not and why: {e!r}")
        e.setdefault("scope", [])
        e["patterns"] = [re.compile(p) for p in e["not"]]
    return entries


def public(entries):
    """The glossary as the user docs read it (site.json terms): no compiled patterns."""
    return [{k: e[k] for k in ("use", "not", "why", "scope", "id") if k in e} for e in entries]


def term(entries, ident):
    """The word of the entry IDENT (the one copy code that sends it compares against)."""
    return next(e["use"] for e in entries if e.get("id") == ident)


def violations(entries, files, root):
    """'path:line: variant -> use' for every line of FILES (paths under ROOT) using a
    refused variant, a scoped entry only on its paths."""
    out = []
    for path in files:
        rel = Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
        if rel in QUOTING:
            continue
        try:
            lines = Path(path).read_text().splitlines()
        except (UnicodeDecodeError, IsADirectoryError):
            continue
        rules = [e for e in entries if not e["scope"] or rel.startswith(tuple(e["scope"]))]
        for n, line in enumerate(lines, 1):
            for e in rules:
                for p in e["patterns"]:
                    m = p.search(line)
                    if m:
                        out.append(f"{rel}:{n}: {m.group(0)!r} -> {e['use']}")
    return out


def config_dir():
    """The site config the check also covers: DEEPOPS_CONFIG_DIR, else config/ here."""
    path = Path(os.environ.get("DEEPOPS_CONFIG_DIR") or ROOT / "config")
    return path if (path / ".git").exists() else None


def scan():
    """Every violation in this repository's tracked files and the site config's."""
    entries = load()
    found = violations(entries, tracked("."), ROOT)
    config = config_dir()
    if config:
        found += [f"config/{v}" for v in violations(entries, tracked(".", root=config), config)]
    return found


def main(argv=None):
    parser = cli.parser(__doc__)
    parser.add_argument("paths", nargs="*", help="files to check instead of both repositories")
    args = parser.parse_args(argv)
    found = violations(load(), [Path(p) for p in args.paths], Path.cwd()) if args.paths else scan()
    if found:
        print("\n".join(found))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
