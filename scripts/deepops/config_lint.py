#!/usr/bin/env python3
"""Keep the example config, the site config and the docs honest.

    config_lint.py              run every check
    config_lint.py CHECK ...    run some: drift, dead, doc-paths, shadow

drift      config/ mirrors config.example/: in each group_vars and host_vars file both
           have, the comment lines match in order and the same variable names appear
           (set or commented). A comment only the site needs starts with "# site:".
           Only line numbers are printed for config/, which is private.
dead       every variable config.example sets or shows is read somewhere: a role,
           playbook, plugin or script, another config.example value, or Kubespray.
           A setting nothing reads misleads whoever sets it.
doc-paths  every repository path a document names (roles/..., playbooks/..., ...)
           exists, so a page never sends a reader to a file that is gone.
shadow     no group or host has its variables in two places (all.yml beside all/, or
           all.yml beside all.yaml): Ansible reads one and drops the other without a
           word, so a whole file of settings can vanish when a directory appears.

CONFIG_DIR selects the site config (default config/); drift is skipped without it.
Under the commit hook (pre-commit sets PRE_COMMIT) the site config is read as its git
repository last committed it: pre-commit stashes this repository's unstaged edits but
never the site's, so its working tree would be judged against the wrong example and
one session's half-done edit would block every other session's commit.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "config.example"
CONFIG = Path(os.environ.get("CONFIG_DIR", ROOT / "config"))
NAME = re.compile(r"^#?\s?([a-z_][a-z0-9_]*):(\s|$)")
PATH = re.compile(
    r"(?<![\w./-])((?:roles|playbooks|scripts|docs|config\.example|tests|skills|plugins)/[\w./-]*[\w/])"
)
# Read by Ansible itself or by tools outside the repository, not by our code.
READ_ELSEWHERE = {"ansible_user", "ansible_host", "ansible_python_interpreter", "ansible_become"}


def tracked(*patterns):
    out = subprocess.run(
        ["git", "ls-files", "--", *patterns], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [ROOT / p for p in out.stdout.split()]


def committed_site():
    """CONFIG as committed, when the commit hook runs and CONFIG is a git repository."""
    return bool(os.environ.get("PRE_COMMIT")) and (CONFIG / ".git").exists()


def site_entries(kind):
    """{name: is a directory} for CONFIG/KIND: committed under the hook, else on disk."""
    if committed_site():
        out = subprocess.run(
            ["git", "ls-tree", "-z", "HEAD", f"{kind}/"],
            cwd=CONFIG, capture_output=True, text=True, check=True,
            env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        ).stdout  # fmt: skip
        entries = {}
        for record in filter(None, out.split("\0")):
            meta, path = record.split("\t", 1)
            entries[path.split("/", 1)[1]] = meta.split()[1] == "tree"
        return entries
    base = CONFIG / kind
    return {p.name: p.is_dir() for p in base.iterdir()} if base.is_dir() else {}


def site_text(rel):
    """CONFIG/REL's text, or None when it does not exist: committed under the hook."""
    if committed_site():
        out = subprocess.run(
            ["git", "show", f"HEAD:{rel}"], cwd=CONFIG, capture_output=True, text=True,
            env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        )  # fmt: skip
        return out.stdout if out.returncode == 0 else None
    path = CONFIG / rel
    return path.read_text() if path.is_file() else None


def var_files(base):
    return sorted(p for p in base.glob("group_vars/*.yml")) + sorted(
        p for p in base.glob("host_vars/*") if p.is_file()
    )


def comment_text(line):
    """The comment a line carries, normalised to "# text", or None for a value line.

    "# text" is a comment; "#  key: x", "#  - x" and "#key: x" are commented-out
    values (config.example shows them commented, config sets them), except that a
    commented value line holding a comment ("#  # text", the comment inside a
    commented mapping) carries that comment, as "  # text" does in config.
    """
    s = line.strip()
    if not s.startswith("#"):
        return None
    body = s[1:]
    if body == "" or (body.startswith(" ") and not body.startswith("  ")):
        return s
    inner = body.strip()
    return inner if inner.startswith("#") and comment_text(inner) else None


def comments(text):
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        text = comment_text(line)
        if text is not None and not NAME.match(line) and not line.startswith("# site:"):
            out.append((i, text))
    return out


def names(text):
    found = {}
    for i, line in enumerate(text.splitlines(), 1):
        m = NAME.match(line)
        if m and not line.startswith("# site:"):
            found.setdefault(m.group(1), i)
    return found


def check_drift():
    if not CONFIG.is_dir():
        return []
    problems = []
    for example in var_files(EXAMPLE):
        rel = example.relative_to(EXAMPLE)
        site = site_text(str(rel))
        if site is None:
            continue
        ce, cs = comments(example.read_text()), comments(site)
        es, ss = [t for _, t in ce], [t for _, t in cs]
        if es != ss:
            import difflib

            for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=es, b=ss, autojunk=False).get_opcodes():
                if op != "equal":
                    where_e = ce[i1][0] if i1 < len(ce) else "end"
                    where_s = cs[j1][0] if j1 < len(cs) else "end"
                    problems.append(
                        f"drift: {rel}: comments differ at config.example line {where_e}, config line {where_s}"
                    )
        ne, ns = names(example.read_text()), names(site)
        for n in sorted(set(ne) - set(ns)):
            problems.append(f"drift: {rel}:{ne[n]}: {n} is in config.example only")
        for n in sorted(set(ns) - set(ne)):
            problems.append(f"drift: {rel}: config line {ns[n]} sets a name config.example lacks")
    return problems


def check_dead():
    sources = []
    for path in tracked("roles", "playbooks", "plugins", "scripts", "config.example"):
        if (
            path.suffix in (".yml", ".yaml", ".j2", ".py", ".sh", ".lua", ".cfg", ".conf", "")
            and path.is_file()
        ):
            sources.append((path, path.read_text(errors="ignore")))
    kubespray = ROOT / "submodules" / "kubespray"
    k8s_text = ""
    if kubespray.is_dir():
        # A commit hook sets GIT_DIR, GIT_INDEX_FILE and friends for this repository;
        # inherited, they would make git grep read our index inside the submodule.
        clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        k8s_text = subprocess.run(
            ["git", "grep", "-h", "-o", "-E", r"[a-z_][a-z0-9_]*", "--", "roles", "playbooks", "inventory"],
            cwd=kubespray,
            capture_output=True,
            text=True,
            env=clean,
        ).stdout
    k8s_words = set(k8s_text.split())
    problems = []
    for example in var_files(EXAMPLE):
        for name, line in names(example.read_text()).items():
            if name in READ_ELSEWHERE or name in k8s_words:
                continue
            pattern = re.compile(rf"(?<![\w]){re.escape(name)}(?![\w])")
            # Its own definition does not count, but another value in the same file
            # that templates it does (the part after "name:").
            used = any(
                pattern.search(
                    text if path != example else "\n".join(NAME.sub("", line) for line in text.splitlines())
                )
                for path, text in sources
            )
            if not used:
                problems.append(f"dead: {example.relative_to(ROOT)}:{line}: nothing reads {name}")
    return problems


def check_doc_paths():
    problems = []
    for doc in tracked("*.md", "skills"):
        # MODERNIZATION.md is the change log: it names files that were removed on purpose.
        if not doc.is_file() or doc.suffix != ".md" or doc.name == "MODERNIZATION.md":
            continue
        for number, line in enumerate(doc.read_text().splitlines(), 1):
            for path in PATH.findall(line):
                if any(c in path for c in "*<>{}") or path.endswith("/..."):
                    continue
                if not (ROOT / path).exists():
                    problems.append(f"doc-paths: {doc.relative_to(ROOT)}:{number}: {path} does not exist")
    return problems


VAR_SUFFIXES = ("", ".yml", ".yaml", ".json")


def check_shadow():
    problems = []
    for base in (EXAMPLE, CONFIG):
        for kind in ("group_vars", "host_vars"):
            if base == CONFIG:
                listing = site_entries(kind)
            else:
                listing = {p.name: p.is_dir() for p in (base / kind).glob("*")}
            entries = {}
            for name, is_dir in sorted(listing.items()):
                stem = (
                    name
                    if is_dir
                    else next((name[: -len(s)] for s in VAR_SUFFIXES[1:] if name.endswith(s)), name)
                )
                entries.setdefault(stem, []).append(name + ("/" if is_dir else ""))
            for stem, found in entries.items():
                if len(found) > 1:
                    where = f"config/{kind}" if base == CONFIG else f"config.example/{kind}"
                    problems.append(
                        f"shadow: {where}: {', '.join(found)} hold {stem}'s variables twice; "
                        "Ansible reads only one, so keep one"
                    )
    return problems


CHECKS = {"drift": check_drift, "dead": check_dead, "doc-paths": check_doc_paths, "shadow": check_shadow}


def main(argv):
    wanted = argv or list(CHECKS)
    unknown = [c for c in wanted if c not in CHECKS]
    if unknown:
        print(f"unknown check: {', '.join(unknown)}", file=sys.stderr)
        return 2
    problems = [p for c in wanted for p in CHECKS[c]()]
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
