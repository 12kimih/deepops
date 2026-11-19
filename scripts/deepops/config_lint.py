#!/usr/bin/env python3
"""Keep the example config, the site config and the docs honest.

    config_lint.py [--fix]              run every check
    config_lint.py [--fix] CHECK ...    run some: commented, redundant, dead, doc-paths,
                                        doc-vars, doc-limits, shadow

A role's defaults own each setting's default and a config states only what differs
(scripts/deepops/settings.py models both):

commented  config.example shows a setting commented at exactly its owner's default
           ("#name: value"), or marks a value a site might set "# example" (always so
           for a setting without a default). --fix writes the default.
redundant  no active line, in config.example or config/, equals what every host it
           decides would get without it, and none is overridden on every host. A site
           pinning a value on purpose says why in a "# site:" comment right above it.
           --fix comments the line out in config.example and deletes it in config/.
dead       every variable config.example or config/ sets or shows, and every role
           default, is read somewhere: a role, playbook, plugin or script, another
           value, or Kubespray. A setting nothing reads misleads whoever sets it (a typo
           in config/ is one), and a default left unread is usually a task that was lost.
doc-paths  every repository path a document names (roles/..., playbooks/..., ...)
           exists, so a page never sends a reader to a file that is gone.
doc-vars   every setting a document tells a reader to set (`name: value`) or lists in
           a table's first column is read somewhere, so a page never names a
           variable that was renamed or removed.
doc-limits every `ansible-playbook -l X playbooks/...` a document shows limits to hosts the
           play targets: -l only narrows a play's own hosts, so a group outside them
           (or one config.example/inventory does not know) runs nothing there.
shadow     no group or host has its variables in two places (all.yml beside all/, or
           all.yml beside all.yaml): Ansible reads one and drops the other without a
           word, so a whole file of settings can vanish when a directory appears.

Only paths and line numbers are printed for config/, which is private.
DEEPOPS_CONFIG_DIR selects the site config (default config/). Under the commit hook
(pre-commit sets PRE_COMMIT) the site config is read as its git repository last
committed it, and never fixed: pre-commit stashes this repository's unstaged edits but
never the site's, so one session's half-done edit would block every other's commit.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "config.example"
CONFIG = Path(os.environ.get("DEEPOPS_CONFIG_DIR", ROOT / "config"))
PATH = re.compile(
    r"(?<![\w./-])((?:roles|playbooks|scripts|docs|config\.example|tests|skills|plugins)/[\w./-]*[\w/])"
)
# The settings deepops no longer reads (roles/retired_settings): naming one there is not a
# read, and a document or config that names one is told what replaced it.
RETIRED_FILE = ROOT / "roles" / "retired_settings" / "vars" / "main.yml"
# Read by Ansible itself or by tools outside the repository, not by our code.
READ_ELSEWHERE = {"ansible_user", "ansible_host", "ansible_python_interpreter", "ansible_become"}


def tracked(*patterns):
    """Tracked files: what a commit holds, never untracked work in the tree."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *patterns], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [ROOT / p for p in out.stdout.split("\0") if p]


def committed_site():
    """Whether to read CONFIG as committed: the commit hook runs and CONFIG is a git
    repository with at least one commit (a fresh one has nothing committed to read yet)."""
    if not (os.environ.get("PRE_COMMIT") and (CONFIG / ".git").exists()):
        return False
    head = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", "HEAD"],
        cwd=CONFIG,
        capture_output=True,
        env=settings.CLEAN_ENV,
    )
    return head.returncode == 0


def configs():
    """(label, Tree, private) for config.example and, when present, the site config."""
    out = [("config.example", settings.Tree(EXAMPLE, tracked_only=False), False)]
    if CONFIG.is_dir():
        rev = "HEAD" if committed_site() else None
        out.append(("config", settings.Tree(CONFIG, rev=rev, tracked_only=False), True))
    return out


class Fixes:
    """Line edits to files, applied bottom-up so earlier line numbers stay valid."""

    def __init__(self):
        self.edits = {}

    def add(self, path, first, last, lines):
        self.edits.setdefault(Path(path), []).append((first, last, lines))

    def apply(self):
        for path, edits in self.edits.items():
            text = path.read_text().splitlines()
            for first, last, lines in sorted(edits, reverse=True):
                text[first - 1 : last] = lines
            path.write_text("\n".join(text) + "\n")
        return sorted(self.edits)


def where(label, rel, entry, private):
    return f"{label}/{rel}:{entry.line}" + ("" if private else f": {entry.name}")


def check_commented(fix=None):
    owners = settings.owners()
    tree = settings.Tree(EXAMPLE, tracked_only=False)
    problems = []
    for src in settings.Config(tree).sources:
        for e in (e for e in src.entries if not e.active):
            owner = owners.get(e.name)
            at = where("config.example", src.rel, e, False)
            if owner is not None and owner.value is settings.AMBIGUOUS:
                continue
            if e.value is settings.MISSING:
                problems.append(f"commented: {at}: the commented value is not YAML")
            elif e.example:
                if owner is not None and e.value == owner.value:
                    problems.append(f"commented: {at}: shows its default, so it is no example; drop the mark")
            elif owner is None:
                problems.append(
                    f"commented: {at}: has no default, so the value is an example: mark it # example"
                )
            elif e.value != owner.value:
                problems.append(
                    f"commented: {at}: shows {settings.show(e.value)}, its default ({owner.paths[0]}) "
                    f"is {settings.show(owner.value)}: show the default, or mark a site's value # example"
                )
                if fix is not None:
                    fix.add(EXAMPLE / src.rel, e.line, e.end, commented(e.name, owner.value))
    return problems


def commented(name, value):
    """NAME: VALUE as commented lines, the way config.example shows a default."""
    return ["#" + line for line in settings.dump({name: value}, block=True).splitlines()]


def check_redundant(fix=None):
    owners = settings.owners()
    problems = []
    for label, tree, private in configs():
        config = settings.Config(tree)
        same, unused = settings.redundant(config, owners)
        base = EXAMPLE if label == "config.example" else CONFIG
        for kind, found in (("redundant", same), ("unused", unused)):
            for src, e in found:
                if e.pinned:
                    continue
                why = (
                    "equals what it would be without it" if kind == "redundant"
                    else "is overridden on every host it applies to"
                )  # fmt: skip
                hint = "comment it out" if label == "config.example" else "delete it, or say why in # site:"
                problems.append(f"{kind}: {where(label, src.rel, e, private)}: {why}; {hint}")
                if fix is not None and tree.rev is None:
                    lines = (tree.text(src.rel) or "").splitlines()[e.line - 1 : e.end]
                    new = (
                        ["#" + line if line else line for line in lines] if label == "config.example" else []
                    )
                    fix.add(base / src.rel, e.line, e.end, new)
    return problems


def retired():
    """{retired name: what replaces it}."""
    return settings.load(RETIRED_FILE.read_text())["retired_settings"]


def read_words():
    """Every identifier our code, the example config and Kubespray contain, or None
    when Kubespray is not checked out."""
    words = set()
    for path in tracked("roles", "playbooks", "plugins", "scripts", "config.example"):
        if path.is_file() and "galaxy" not in path.parts and path != RETIRED_FILE:
            words.update(re.findall(r"[a-z_][a-z0-9_]*", path.read_text(errors="ignore")))
    kubespray = ROOT / "submodules" / "kubespray"
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    k8s = subprocess.run(
        ["git", "grep", "-h", "-o", "-E", r"[a-z_][a-z0-9_]*", "--", "roles", "playbooks", "inventory"],
        cwd=kubespray, capture_output=True, text=True, env=clean,
    )  # fmt: skip
    if not (kubespray / "cluster.yml").is_file() or k8s.returncode != 0:
        return None
    return words | set(k8s.stdout.split())


# A setting a page tells the reader to set, the first cell of a table row, and a setting
# line of a YAML code block (```yaml: a page shows settings as YAML).
SETTING = re.compile(r"`([a-z][a-z0-9]*_[a-z0-9_]+): [^`]+`")
TABLE_NAME = re.compile(r"^\|\s*`([a-z][a-z0-9]*_[a-z0-9_]+)`")
PROSE_SETTING = re.compile(r"^([a-z][a-z0-9]*_[a-z0-9_]+): \S.*$")
BLOCK_SETTING = re.compile(r"^\s*#?\s?([a-z][a-z0-9]*_[a-z0-9_]+): ")


def doc_settings(text):
    """(line number, setting name) for every setting a page names."""
    fence = None  # the open code block's language
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fence = None if fence is not None else line.lstrip()[3:].strip().lower()
            continue
        if fence is not None:
            if fence not in ("yaml", "yml"):
                continue
            names = BLOCK_SETTING.findall(line)
        elif line.startswith("|"):
            names = TABLE_NAME.findall(line)
        else:  # inline `name: value`, or a line that is nothing but one
            names = SETTING.findall(line) or PROSE_SETTING.findall(line)
        for name in names:
            yield number, name


def check_doc_vars():
    words = read_words()
    if words is None:
        return ["doc-vars: submodules/kubespray is not checked out (git submodule update --init)"]
    gone, problems = retired(), []
    for doc in tracked("*.md"):
        # MODERNIZATION.md is the change log: it names settings that were removed.
        if not doc.is_file() or doc.name == "MODERNIZATION.md":
            continue
        for number, name in doc_settings(doc.read_text()):
            where = f"doc-vars: {doc.relative_to(ROOT)}:{number}"
            if name in gone:
                problems.append(f"{where}: {name} is retired: set {gone[name]} instead")
            elif name not in words:
                problems.append(f"{where}: nothing reads {name}")
    return problems


def check_dead():
    sources = []
    for path in tracked("roles", "playbooks", "plugins", "scripts", "config.example"):
        # A role's templates are read whatever their suffix (a .tok, a .service).
        if (
            (
                path.suffix in (".yml", ".yaml", ".j2", ".py", ".sh", ".lua", ".cfg", ".conf", "")
                or "templates" in path.parts
            )
            and path.is_file()
            and path != RETIRED_FILE
        ):
            sources.append((path, path.read_text(errors="ignore")))
    # Kubespray reads many config.example settings. Without its checkout every one of
    # them would read as dead, so a missing submodule is a failure of its own.
    kubespray = ROOT / "submodules" / "kubespray"
    if not (kubespray / "cluster.yml").is_file():
        return ["dead: submodules/kubespray is not checked out (git submodule update --init)"]
    # A commit hook sets GIT_DIR, GIT_INDEX_FILE and friends for this repository;
    # inherited, they would make git grep read our index inside the submodule.
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    k8s = subprocess.run(
        ["git", "grep", "-h", "-o", "-E", r"[a-z_][a-z0-9_]*", "--", "roles", "playbooks", "inventory"],
        cwd=kubespray,
        capture_output=True,
        text=True,
        env=clean,
    )
    if k8s.returncode != 0:
        return [f"dead: cannot read Kubespray's variables: {k8s.stderr.strip() or 'git grep found nothing'}"]
    k8s_words = set(k8s.stdout.split())
    problems = []
    # (key, report as, private, {name: line}, text): the config files and role defaults
    # whose names must be read, each also a source for the others.
    named = []
    for label, tree, private in configs():
        for src in settings.Config(tree).sources:
            found = {}
            for e in src.entries:
                found.setdefault(e.name, e.line)
            key = f"{label}/{src.rel}"
            named.append((key, key, private, found, tree.text(src.rel) or ""))
    for path in [p for p in tracked("roles/*/defaults/main.yml") if "galaxy" not in p.parts]:
        text = path.read_text()
        found = {e.name: e.line for e in settings.entries(text) if e.active}
        named.append((str(path), str(path.relative_to(ROOT)), False, found, text))
    keys = {key for key, *_ in named}
    texts = [(key, text) for key, _, _, _, text in named]
    texts += [(str(path), text) for path, text in sources if source_key(path) not in keys]
    # The site's own templates and files (files/slurm/*.conf, ...) read its settings too.
    for label, tree, _ in configs():
        if label == "config":
            texts += [
                (f"config/{rel}", tree.text(rel) or "") for rel in tree.files() if f"config/{rel}" not in keys
            ]
    for key, shown, private, found, own in named:
        # Its own definition does not count, but another value in the same file that
        # templates it does (the part after "name:").
        rest = "\n".join(re.sub(r"^#?[A-Za-z_]\w*:", "", raw) for raw in own.splitlines())
        for name, line in found.items():
            if name in READ_ELSEWHERE or name in k8s_words:
                continue
            pattern = re.compile(rf"(?<![\w]){re.escape(name)}(?![\w])")
            if pattern.search(rest) or any(pattern.search(text) for k, text in texts if k != key):
                continue
            problems.append(
                f"dead: {shown}:{line}" + (": nothing reads it" if private else f": nothing reads {name}")
            )
    return problems


def source_key(path):
    """PATH's key among the named files: config.example/<rel> for the example, else itself."""
    return f"config.example/{path.relative_to(EXAMPLE)}" if EXAMPLE in path.parents else str(path)


def check_doc_paths():
    # A path counts when git tracks it (a file, or a directory holding tracked files):
    # a doc naming a file only this checkout has would pass here and fail in CI.
    known = set()
    for path in tracked():
        rel = path.relative_to(ROOT)
        known.add(str(rel))
        known.update(str(parent) for parent in rel.parents)
    problems = []
    for doc in tracked("*.md", "skills"):
        # MODERNIZATION.md is the change log: it names files that were removed on purpose.
        if not doc.is_file() or doc.suffix != ".md" or doc.name == "MODERNIZATION.md":
            continue
        for number, line in enumerate(doc.read_text().splitlines(), 1):
            for path in PATH.findall(line):
                if any(c in path for c in "*<>{}") or path.endswith("/..."):
                    continue
                if path.rstrip("/") not in known:
                    problems.append(f"doc-paths: {doc.relative_to(ROOT)}:{number}: {path} does not exist")
    return problems


VAR_SUFFIXES = ("", ".yml", ".yaml", ".json")


def example_groups():
    """(groups, {group: its child groups}, every name) of config.example/inventory."""
    children, groups, current = {}, set(), None
    text = (EXAMPLE / "inventory").read_text()
    for line in text.splitlines():
        header = re.match(r"^\[([^\]:]+)(:children)?\]", line)
        if header:
            current = header.group(1) if header.group(2) else None
            groups.add(header.group(1))
            children.setdefault(header.group(1), set())
        elif current and line.strip() and not line.lstrip().startswith("#"):
            children[current].add(line.strip())
    return groups, children, set(re.findall(r"[A-Za-z0-9_.-]+", text))


def play_targets(playbook):
    """The group each play of PLAYBOOK targets (a hostlist default counts as the group)."""
    data = settings.load(playbook.read_text()) or []
    hosts = [play.get("hosts") for play in data if isinstance(play, dict) and "hosts" in play]
    return [
        re.search(r"default\('([^']+)'\)", str(h)).group(1) if "default(" in str(h) else str(h) for h in hosts
    ]


def check_doc_limits():
    groups, children, names = example_groups()

    def reach(group, seen=None):
        seen = {group} if seen is None else seen
        for child in children.get(group, ()):
            if child not in seen:
                seen.add(child)
                reach(child, seen)
        return seen

    problems = []
    command = re.compile(r"ansible-playbook\b[^\n`]*")
    for doc in tracked("*.md"):
        if not doc.is_file():
            continue
        for number, line in enumerate(doc.read_text(errors="ignore").splitlines(), 1):
            for cmd in command.findall(line):
                limit = re.search(r"(?:-l|--limit)[ =](\S+)", cmd)
                playbook = re.search(r"(playbooks/[\w./-]+\.ya?ml)", cmd)
                if not limit or not playbook or "hostlist" in cmd or not (ROOT / playbook.group(1)).is_file():
                    continue
                targets = play_targets(ROOT / playbook.group(1))
                if not targets:  # a playbook of imports: each imported play checks its own
                    continue
                for name in re.split(r"[,:]", limit.group(1).strip("'\"")):
                    name = name.lstrip("!&")
                    if not name or re.search(r"[<>{}$*]", name) or name == "localhost":
                        continue
                    where = f"doc-limits: {doc.relative_to(ROOT)}:{number}: -l {name}"
                    if name not in names:
                        problems.append(f"{where}: config.example/inventory has no such group or host")
                    elif name in groups and not any(
                        t == "all" or t not in groups or reach(name) & reach(t) for t in targets
                    ):
                        problems.append(
                            f"{where}: outside {playbook.group(1)}'s hosts ({', '.join(targets)})"
                        )
    return problems


def check_shadow():
    problems = []
    for label, tree, _ in configs():
        for kind in ("group_vars", "host_vars"):
            listing = tree.entries(kind)
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
                    where = f"{label}/{kind}"
                    problems.append(
                        f"shadow: {where}: {', '.join(found)} hold {stem}'s variables twice; "
                        "Ansible reads only one, so keep one"
                    )
    return problems


# A check that can fix takes a Fixes to collect its edits in.
FIXABLE = {"commented", "redundant"}
CHECKS = {
    "commented": check_commented,
    "redundant": check_redundant,
    "dead": check_dead,
    "doc-paths": check_doc_paths,
    "doc-vars": check_doc_vars,
    "doc-limits": check_doc_limits,
    "shadow": check_shadow,
}


def main(argv):
    fix = Fixes() if "--fix" in argv else None
    wanted = [a for a in argv if a != "--fix"] or list(CHECKS)
    unknown = [c for c in wanted if c not in CHECKS]
    if unknown:
        print(f"unknown check: {', '.join(unknown)}", file=sys.stderr)
        return 2
    problems = [p for c in wanted for p in (CHECKS[c](fix) if c in FIXABLE else CHECKS[c]())]
    if fix is not None:
        for path in fix.apply():
            print(f"fixed {path}", file=sys.stderr)
        return main([a for a in argv if a != "--fix"])
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
