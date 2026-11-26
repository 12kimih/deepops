#!/usr/bin/env python3
"""Keep the example config, the site config and the docs honest.

    config_lint.py [--fix]              run every check
    config_lint.py [--fix] CHECK ...    run some: commented, redundant, dead, doc-paths,
                                        doc-vars, doc-limits, doc-anchors, doc-repeats, doc-gpu,
                                        duplicates, shadow, broadcast,
                                        copies

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
           value, or Kubespray (for config/ only when its inventory has Kubernetes hosts:
           a Slurm-only site's Kubespray settings are read by nothing it runs). A setting
           nothing reads misleads whoever sets it (a typo in config/ is one), and a
           default left unread is usually a task that was lost.
doc-paths  every repository path a document names (roles/..., playbooks/..., ...)
           exists, so a page never sends a reader to a file that is gone.
doc-vars   every setting a document tells a reader to set (`name: value`) or lists in
           a table's first column is read somewhere, so a page never names a
           variable that was renamed or removed.
doc-limits every `ansible-playbook -l X playbooks/...` a document shows limits to hosts the
           play targets: -l only narrows a play's own hosts, so a group outside them
           (or one config.example/inventory does not know) runs nothing there.
doc-anchors every link to a section (page.md#heading, or #heading on the same page) names
           a heading the target page has, by GitHub's slug of it: a renamed or
           removed section left a table of contents pointing nowhere.
doc-repeats no document holds the same run of DOC_REPEAT text lines twice (code blocks
           aside, where a sample output may repeat): a section pasted in again by an
           edit (one page carried four sections twice, the copies already apart).
doc-gpu    every GPU command a document runs on a node through ansible or ssh goes
           through systemd-run: the Slurm login setup hides the GPUs from the ssh
           service, so nvidia-smi there finds none (the roles' tasks do the same).
retired    no config sets a setting deepops retired (the deploy would stop on it).
removals   deepops_component_removals names only components with removal: explicit
           in the registry (any other name fails every deploy).
duplicates no variable file sets a key twice in one mapping: Ansible keeps the last and
           only warns, so the first value is lost without a failure.
broadcast  no config value mentions a whole channel (<!channel>, <!here>, <!everyone>):
           what the administrators must act on mentions their group
           (slack_admin_group_id, roles/slack_defaults), everyone else is told by DM.
shadow     no group or host has its variables in two places (all.yml beside all/, or
           all.yml beside all.yaml): Ansible reads one and drops the other without a
           word, so a whole file of settings can vanish when a directory appears.
copies     no site file equals config.example's: but for the inventory and its variables,
           which Ansible reads from the inventory directory alone, config.example's
           file is what the tools read while the site has none (scripts/common.sh
           deepops_config_file), so a copy says nothing and hides the next update.
           --fix deletes it.

Only paths and line numbers are printed for config/, which is private.
DEEPOPS_CONFIG_DIR selects the site config (default config/). Under the commit hook
(pre-commit sets PRE_COMMIT) the site config is read as its git repository last
committed it, and never fixed: pre-commit stashes this repository's unstaged edits but
never the site's, so one session's half-done edit would block every other's commit.
The site's own commit hook (scripts/deepops/config-pre-commit.sh, installed by
scripts/setup.sh) passes what it is committing as DEEPOPS_SITE_REV.
"""

import ast
import io
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402
import settings  # noqa: E402
from repo_files import tracked  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "config.example"
CONFIG = Path(os.environ.get("DEEPOPS_CONFIG_DIR", ROOT / "config"))
# ASCII: a path is ASCII, and a Korean particle written against one ("scripts/check.sh가")
# is not part of it (Python's \w would take Hangul too).
PATH = re.compile(
    r"(?<![\w./-])((?:roles|playbooks|scripts|docs|config\.example|tests|skills|plugins)/[\w./-]*[\w/])",
    re.ASCII,
)
# The settings deepops no longer reads (roles/retired_settings): naming one there is not a
# read, and a document or config that names one is told what replaced it.
RETIRED_FILE = ROOT / "roles" / "retired_settings" / "vars" / "main.yml"
# What Ansible reads from the inventory directory alone (scripts/setup.sh starts a site
# config with them): config.example's copy of these is never read for a site.
INVENTORY_DIR = ("inventory", "group_vars", "host_vars")
# Read by Ansible itself or by tools outside the repository, not by our code.
READ_ELSEWHERE = {"ansible_user", "ansible_host", "ansible_python_interpreter", "ansible_become"}


def site_rev():
    """The revision of CONFIG to read (settings.site_rev), or None for its working tree."""
    return settings.site_rev(CONFIG)


def configs():
    """(label, Tree, private) for config.example and, when present, the site config."""
    out = [("config.example", settings.Tree(EXAMPLE, tracked_only=False), False)]
    if CONFIG.is_dir():
        out.append(("config", settings.Tree(CONFIG, rev=site_rev(), tracked_only=False), True))
    return out


def known_paths(files):
    """Each of FILES (relative paths) and every directory holding one."""
    known = set()
    for rel in files:
        known.add(rel)
        known.update(str(parent) for parent in Path(rel).parents)
    return known


def documents():
    """(shown, text, site) for each Markdown page that names deepops' paths, settings
    and commands: deepops' own and the site config's (its admin and user guides hand out
    deepops' playbooks too; config.example's are deepops' own already). `site` is None
    for deepops' pages, else (the site's own paths, its inventory's words): a site page
    also names its own files (its docs/) and hosts (-l <node>). A site page is private."""
    for doc in tracked("*.md"):
        if doc.is_file():
            yield str(doc.relative_to(ROOT)), doc.read_text(errors="ignore"), None
    for _, tree, private in configs():
        if not private:
            continue
        files = tree.files()
        site = (known_paths(files), set(re.findall(r"[A-Za-z0-9_.-]+", tree.text("inventory") or "")))
        for rel in files:
            text = tree.text(rel) if rel.endswith(".md") else None
            if text is not None:
                yield f"config/{rel}", text, site


def found(check, shown, number, what, site):
    """One problem line; a site page shows only where (config/ is private)."""
    return f"{check}: {shown}:{number}" + ("" if site else f": {what}")


class Fixes:
    """Line edits to files, applied bottom-up so earlier line numbers stay valid."""

    def __init__(self):
        self.edits = {}
        self.removals = set()

    def add(self, path, first, last, lines):
        self.edits.setdefault(Path(path), []).append((first, last, lines))

    def remove(self, path):
        self.removals.add(Path(path))

    def apply(self):
        for path, edits in self.edits.items():
            text = path.read_text().splitlines()
            for first, last, lines in sorted(edits, reverse=True):
                text[first - 1 : last] = lines
            path.write_text("\n".join(text) + "\n")
        for path in self.removals:
            path.unlink()
        return sorted(set(self.edits) | self.removals)


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
        cwd=kubespray, capture_output=True, text=True, errors="replace", env=clean,
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
    for shown, text, site in documents():
        for number, name in doc_settings(text):
            if name in gone:
                problems.append(
                    found("doc-vars", shown, number, f"{name} is retired: set {gone[name]} instead", site)
                )
            elif name not in words:
                problems.append(found("doc-vars", shown, number, f"nothing reads {name}", site))
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
        errors="replace",
        env=clean,
    )
    if k8s.returncode != 0:
        return [f"dead: cannot read Kubespray's variables: {k8s.stderr.strip() or 'git grep found nothing'}"]
    k8s_words = set(k8s.stdout.split())
    problems = []
    # (key, report as, private, {name: line}, text): the config files and role defaults
    # whose names must be read, each also a source for the others.
    named = []
    # Kubespray reads a site's settings only where the site runs it.
    kubernetes = {
        label: label == "config.example" or bool(group_hosts(tree.text("inventory") or "", "k8s_cluster"))
        for label, tree, _ in configs()
    }
    for label, tree, private in configs():
        idle = idle_vars(tree) if private else {}
        problems += [f"dead: {label}/{rel}: {why}" for rel, why in sorted(idle.items())]
        for src in settings.Config(tree).sources:
            if src.rel in idle:  # the whole file is reported above
                continue
            found = {}
            for e in src.entries:
                found.setdefault(e.name, e.line)
            key = f"{label}/{src.rel}"
            named.append((key, key, private, found, tree.text(src.rel) or "", kubernetes[label]))
    for path in tracked("roles/*/defaults/main.yml"):
        text = path.read_text()
        found = {e.name: e.line for e in settings.entries(text) if e.active}
        named.append((str(path), str(path.relative_to(ROOT)), False, found, text, True))
    keys = {key for key, *_ in named}
    texts = [(key, text) for key, _, _, _, text, _ in named]
    texts += [(str(path), text) for path, text in sources if source_key(path) not in keys]
    # The site's own templates and files (files/slurm/*.conf, ...) read its settings too.
    for label, tree, _ in configs():
        if label == "config":
            texts += [
                (f"config/{rel}", tree.text(rel) or "") for rel in tree.files() if f"config/{rel}" not in keys
            ]
    for key, shown, private, found, own, k8s in named:
        # Its own definition does not count, but another value in the same file that
        # templates it does (the part after "name:").
        rest = "\n".join(re.sub(r"^#?[A-Za-z_]\w*:", "", raw) for raw in own.splitlines())
        for name, line in found.items():
            if name in READ_ELSEWHERE or (k8s and name in k8s_words):
                continue
            pattern = re.compile(rf"(?<![\w]){re.escape(name)}(?![\w])")
            # Another config is no reader: config.example shows a setting, it does not read
            # the site's, and the site's copy does not read the example's.
            readers = (text for k, text in texts if k != key and config_of(k) in (None, config_of(key)))
            if pattern.search(rest) or any(pattern.search(text) for text in readers):
                continue
            problems.append(
                f"dead: {shown}:{line}" + (": nothing reads it" if private else f": nothing reads {name}")
            )
    return problems


def idle_vars(tree):
    """{file: why} for TREE's group_vars of a group without hosts and host_vars of a host
    outside its inventory: Ansible applies them to no host, so every value in them is read
    by nothing the site runs (a Slurm-only site's Kubernetes group_vars). Only the site
    config: config.example's inventory lists its hosts commented out."""
    inventory = tree.text("inventory") or ""
    _, hosts, _ = inventory_groups(inventory)
    everyone = set().union(*hosts.values())
    idle = {}
    for rel in tree.files():
        parts = Path(rel).parts
        if len(parts) < 2 or parts[0] not in ("group_vars", "host_vars"):
            continue
        name = Path(parts[1]).stem if len(parts) == 2 else parts[1]
        if parts[0] == "group_vars" and name != "all" and not group_hosts(inventory, name):
            idle[rel] = f"group {name} has no hosts in the inventory"
        elif parts[0] == "host_vars" and name not in everyone:
            idle[rel] = f"host {name} is not in the inventory"
    return idle


def config_of(key):
    """Which config a key of check_dead's files belongs to: config.example, config or None."""
    if key.startswith("config.example/") or key.startswith(f"{EXAMPLE}/"):
        return "config.example"
    return "config" if key.startswith("config/") else None


def source_key(path):
    """PATH's key among the named files: config.example/<rel> for the example, else itself."""
    return f"config.example/{path.relative_to(EXAMPLE)}" if EXAMPLE in path.parents else str(path)


# Paths a code comment names that are not this repository's, and why (doc-paths).
NOT_OURS = {
    "scripts/notices.py": "the user docs site's (cluster-docs)",
    "scripts/agent_rules.py": "the user docs site's (cluster-docs)",
    "docs/sidebar/md5sum.txt": "NVIDIA's download site",
    "roles/docker-rootless": "retired (roles/retired_settings)",
    "roles/galaxy": "where Galaxy installs roles, git-ignored (roles/requirements.yml)",
}
CODE = re.compile(r"\.(py|ya?ml|sh|j2|cfg|toml)$|^scripts/|/files/")


def comment_lines(rel, text):
    """{line: text} of the comments of the code file REL, and in Python its docstrings."""
    lines = {}
    if rel.endswith(".py"):
        try:
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.COMMENT:
                    lines[tok.start[0]] = tok.string
            tree = ast.parse(text)
        except (SyntaxError, tokenize.TokenError):
            return lines
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and body:
                doc = body[0]
                if (
                    isinstance(doc, ast.Expr)
                    and isinstance(doc.value, ast.Constant)
                    and isinstance(doc.value.value, str)
                ):
                    for i, line in enumerate(doc.value.value.splitlines()):
                        lines[doc.lineno + i] = lines.get(doc.lineno + i, "") + " " + line
    else:
        for number, line in enumerate(text.splitlines(), 1):
            comment = re.search(r"(?:^|\s)#(.*)$", line)
            if comment:
                lines[number] = comment.group(1)
    return lines


def code_comments():
    """(shown, {line: text}) for each tracked code file: its comments and docstrings,
    where paths are named as in a page (plugins/filter/slurm_conf.py named a script that
    never existed, 2026-11-27)."""
    for path in tracked():
        rel = str(path.relative_to(ROOT))
        if not CODE.search(rel) or rel.endswith(".md") or path.is_symlink() or not path.is_file():
            continue
        try:
            lines = comment_lines(rel, path.read_text())
        except UnicodeDecodeError:
            continue
        if lines:
            yield rel, lines


def placeholder(path):
    """A path that stands for any (roles/ROLE/defaults, tests/x/test_y.py)."""
    return any(
        part in ("x", "y") or any(word.isupper() for word in re.split(r"[-_.]", part) if word.isalpha())
        for part in path.split("/")
    )


def check_doc_paths():
    # A path counts when git tracks it (a file, or a directory holding tracked files):
    # a doc naming a file only this checkout has would pass here and fail in CI.
    known = known_paths(str(path.relative_to(ROOT)) for path in tracked())
    problems = []
    for shown, text, site in documents():
        for number, line in enumerate(text.splitlines(), 1):
            for path in PATH.findall(line):
                if any(c in path for c in "*<>{}") or path.endswith("/..."):
                    continue
                if path.rstrip("/") not in known and not (site and path.rstrip("/") in site[0]):
                    problems.append(found("doc-paths", shown, number, f"{path} does not exist", site))

    def module(path):
        # a Python module or one of its names, by path (plugins/action/systemd_unit,
        # tests/lib/in_tree.load)
        return f"{path}.py" in known or f"{path.rsplit('.', 1)[0]}.py" in known

    for shown, lines in code_comments():
        for number, line in sorted(lines.items()):
            for path in PATH.findall(line):
                path = path.rstrip("/")
                if any(c in path for c in "*<>{}") or "..." in path or placeholder(path):
                    continue
                if path not in known and path not in NOT_OURS and not module(path):
                    problems.append(found("doc-paths", shown, number, f"{path} does not exist", None))
    return problems


VAR_SUFFIXES = ("", ".yml", ".yaml", ".json")


def example_groups():
    """(groups, {group: its child groups}, every name) of config.example/inventory."""
    text = (EXAMPLE / "inventory").read_text()
    groups, _, children = inventory_groups(text)
    return groups, children, set(re.findall(r"[A-Za-z0-9_.-]+", text))


def inventory_groups(text):
    """(groups, {group: its own hosts}, {group: its child groups}) of an INI inventory;
    a [group:vars] section holds no hosts."""
    groups, hosts, children, section = set(), {}, {}, None
    for line in text.splitlines():
        header = re.match(r"^\[([^\]:]+)(?::(\w+))?\]", line)
        if header:
            name, kind = header.groups()
            groups.add(name)
            hosts.setdefault(name, set())
            children.setdefault(name, set())
            section = {None: hosts, "children": children}.get(kind), name
        elif section and section[0] is not None and line.strip() and not line.lstrip().startswith(("#", ";")):
            section[0][section[1]].add(line.split()[0])
    return groups, hosts, children


def group_hosts(text, group):
    """The hosts GROUP of the INI inventory TEXT holds, its child groups' included."""
    _, hosts, children = inventory_groups(text)
    found, todo, seen = set(), [group], set()
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            found |= hosts.get(name, set())
            todo.extend(children.get(name, ()))
    return found


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
    for shown, text, site in documents():
        hosts = names | site[1] if site else names
        for number, line in enumerate(text.splitlines(), 1):
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
                    if name not in hosts:
                        why = f"-l {name}: the inventory has no such group or host"
                        problems.append(found("doc-limits", shown, number, why, site))
                    elif name in groups and not any(
                        t == "all" or t not in groups or reach(name) & reach(t) for t in targets
                    ):
                        why = f"-l {name}: outside {playbook.group(1)}'s hosts ({', '.join(targets)})"
                        problems.append(found("doc-limits", shown, number, why, site))
    return problems


# A command a document runs on a node over ansible (ad hoc -a) or ssh, and the GPU
# tools in it that need the devices (dcgmi talks to its daemon and needs none).
REMOTE_COMMAND = re.compile(r"\bansible\b[^`\n]*?\s-a\s[^`\n]*|\bssh\s[^`\n|]*")
GPU_TOOL = re.compile(r"\b(nvidia-smi|nvidia-mig-parted)\b")


# A Markdown link's target and section: [text](page.md#heading) or [text](#heading).
LINK = re.compile(r"\]\(([^)\s#]*)#([^)\s]+)\)")
FENCE = re.compile(r"^\s*(```|~~~)")


def slugs(text):
    """The anchors GitHub gives TEXT's headings (lower case, punctuation dropped, spaces as
    hyphens, -1, -2 for repeats) and the explicit ones (<a id/name="...">)."""
    found, seen, fenced = set(), {}, False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        heading = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if heading:
            title = re.sub(r"`|\*\*|__|\[([^\]]*)\]\([^)]*\)", r"\1", heading.group(1))
            slug = re.sub(r"[^\w\- ]", "", title.strip().lower()).replace(" ", "-")
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            found.add(slug if count == 0 else f"{slug}-{count}")
        found.update(re.findall(r"<a\s+(?:id|name)=[\"']([^\"']+)[\"']", line))
    return found


def check_doc_anchors():
    pages = {shown: (text, site) for shown, text, site in documents()}
    anchors = {shown: slugs(text) for shown, (text, _) in pages.items()}
    problems = []
    for shown, (text, site) in pages.items():
        fenced = False
        for number, line in enumerate(text.splitlines(), 1):
            if FENCE.match(line):
                fenced = not fenced
                continue
            if fenced:
                continue
            for target, section in LINK.findall(line):
                if "://" in target:
                    continue
                page = os.path.normpath(os.path.join(os.path.dirname(shown), target)) if target else shown
                if page in anchors and section.lower() not in anchors[page]:
                    problems.append(
                        found("doc-anchors", shown, number, f"{page} has no section #{section}", site)
                    )
    return problems


# How many consecutive text lines a repeat must span to count.
DOC_REPEAT = 8


def repeats(text):
    """(line, earlier line) of the first run of DOC_REPEAT non-blank lines outside code
    blocks that TEXT already held, not overlapping it; None when there is none."""
    lines, fenced = [], False
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced and line.strip():
            lines.append((number, line.rstrip()))
    seen = {}
    for k in range(len(lines) - DOC_REPEAT + 1):
        run = tuple(line for _, line in lines[k : k + DOC_REPEAT])
        first = seen.setdefault(run, k)
        if k - first >= DOC_REPEAT:
            return lines[k][0], lines[first][0]
    return None


def check_doc_repeats():
    problems = []
    for shown, text, site in documents():
        hit = repeats(text)
        if hit:
            problems.append(found("doc-repeats", shown, hit[0], f"repeats lines from {hit[1]} on", site))
    return problems


def check_doc_gpu():
    problems = []
    for shown, text, site in documents():
        for number, line in enumerate(text.splitlines(), 1):
            for cmd in REMOTE_COMMAND.findall(line):
                if GPU_TOOL.search(cmd) and "systemd-run" not in cmd:
                    why = "runs a GPU tool over ssh, where the GPUs are hidden: wrap it in systemd-run"
                    problems.append(found("doc-gpu", shown, number, why, site))
    return problems


def check_retired():
    """A config that still sets a retired setting stops every deploy at the
    retired_settings role: the change that retires a setting fixes the configs too."""
    gone, problems = retired(), []
    for label, tree, _ in configs():
        for src in settings.Config(tree).sources:
            for e in src.entries:
                if e.active and e.name in gone:
                    problems.append(
                        f"retired: {label}/{src.rel}:{e.line}: {e.name} is retired: set {gone[e.name]} instead"
                    )
    # The extra-vars files deepops ships (-e @virtual/vars_files/...) stop a deploy alike.
    for path in tracked("virtual/vars_files"):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            name = line.split(":", 1)[0]
            if ":" in line and not line.startswith((" ", "#", "-")) and name in gone:
                problems.append(
                    f"retired: {path.relative_to(ROOT)}:{number}: {name} is retired: set {gone[name]} instead"
                )
    return problems


# Paths retired with what replaces them: a config or doc still naming one points people
# at nothing.
RETIRED_PATHS = {
    "/sw/docs": "the user docs site (cluster_info.docs_url) and the portal's overview page",
    "config/docs": "the user docs site (cluster_info.docs_url)",
}


def check_retired_paths():
    """No config file or doc names a retired path (RETIRED_PATHS)."""
    problems = []
    for label, tree, _ in configs():
        for path in sorted(p for p in Path(tree.root).rglob("*") if p.is_file() and ".git" not in p.parts):
            if path.suffix not in ("", ".yml", ".yaml", ".md", ".j2", ".txt"):
                continue
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if "cluster_tools_retired_shared_dirs" in line:
                    continue  # the setting that removes them names them
                for old, new in RETIRED_PATHS.items():
                    if old in line:
                        problems.append(
                            f"retired-paths: {label}/{path.relative_to(tree.root)}:{number}: {old} is gone: {new}"
                        )
    return problems


REGISTRY_FILE = ROOT / "roles" / "deploy_defaults" / "vars" / "main.yml"


def check_removals():
    """deepops_component_removals may name only components with removal: explicit in
    the registry; any other name fails every deploy (plugins/filter/components.py). A
    config that names one before the code defining it lands breaks deploys at once."""
    registry = settings.load(REGISTRY_FILE.read_text())["deepops_component_registry"]
    explicit = {name for name, c in registry.items() if (c or {}).get("removal") == "explicit"}
    problems = []
    for label, tree, _ in configs():
        for src in settings.Config(tree).sources:
            for e in src.entries:
                if e.active and e.name == "deepops_component_removals" and isinstance(e.value, list):
                    problems += [
                        f"removals: {label}/{src.rel}:{e.line}: {name} is not a component with removal: explicit"
                        for name in e.value
                        if name not in explicit
                    ]
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


def check_duplicates():
    problems = []
    for label, tree, private in configs():
        for rel in tree.files():
            if rel.split("/", 1)[0] not in ("group_vars", "host_vars"):
                continue
            for number, key in settings.duplicate_keys(tree.text(rel)):
                problems.append(
                    found(
                        "duplicates",
                        f"{label}/{rel}",
                        number,
                        f"{key} is set again here; Ansible drops the first",
                        private,
                    )
                )
    return problems


# A proxy URL with a user and password (http://user:pass@proxy) in proxy_env reaches every
# task that uses it through `environment:`, which Ansible puts on the module's command
# line, readable in ps by every user of the host.
PROXY_CREDENTIALS = re.compile(r"(?i)proxy[^#\n]*://[^/\s'\"@]+@")


def check_proxy_credentials():
    problems = []
    for label, tree, private in configs():
        for rel in tree.files():
            if rel.split("/", 1)[0] not in ("group_vars", "host_vars"):
                continue
            for number, line in enumerate((tree.text(rel) or "").splitlines(), 1):
                if not line.lstrip().startswith("#") and PROXY_CREDENTIALS.search(line):
                    problems.append(
                        found(
                            "proxy-credentials",
                            f"{label}/{rel}",
                            number,
                            "a proxy URL holds credentials, which `environment:` puts on every "
                            "task's command line; use a proxy that needs none",
                            private,
                        )
                    )
    return problems


# A Slack mention of everyone in a channel, as a message's text writes it.
BROADCAST = re.compile(r"<!(?:channel|here|everyone)\b")


def check_broadcast():
    problems = []
    for label, tree, private in configs():
        for rel in tree.files():
            if rel.split("/", 1)[0] not in ("group_vars", "host_vars"):
                continue
            for number, line in enumerate((tree.text(rel) or "").splitlines(), 1):
                if not line.lstrip().startswith("#") and BROADCAST.search(line):
                    problems.append(
                        found(
                            "broadcast",
                            f"{label}/{rel}",
                            number,
                            "a value mentions a whole channel; notices to the administrators "
                            "mention their group (slack_admin_group_id, roles/slack_defaults)",
                            private,
                        )
                    )
    return problems


# A config value naming a key of a mapping setting (`{{ cluster_info.dashboard_url }}`)
# the setting does not have: Ansible fails the task that renders it, on the hosts, at
# deploy time (a login banner that named a key moved elsewhere failed every node).
REFERENCE = re.compile(r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b(?!\s*\()")
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")  # a path or a word in a template, not a reference


def mapping_keys(trees):
    """{setting: its keys} for each setting a role default or a config sets to a mapping."""
    keys = {}
    for path in tracked("roles/*/defaults/main.yml"):
        for name, value in (settings.load(path.read_text()) or {}).items():
            if isinstance(value, dict):
                keys.setdefault(name, set()).update(value)
    for tree in trees:
        for rel in tree.files():
            if rel.split("/", 1)[0] in ("group_vars", "host_vars"):
                for name, value in (settings.load(tree.text(rel) or "") or {}).items():
                    if isinstance(value, dict) and name in keys:
                        keys[name].update(value)
    return keys


def check_mapping_keys():
    trees = configs()
    keys = mapping_keys([tree for _, tree, _ in trees])
    problems = []
    for label, tree, private in trees:
        for rel in tree.files():
            if rel.split("/", 1)[0] not in ("group_vars", "host_vars"):
                continue
            for number, line in enumerate((tree.text(rel) or "").splitlines(), 1):
                for template in JINJA.findall(line):
                    for name, key in REFERENCE.findall(QUOTED.sub("''", template)):
                        if name in keys and key not in keys[name]:
                            problems.append(
                                found(
                                    "mapping-keys",
                                    f"{label}/{rel}",
                                    number,
                                    f"{name} has no key {key} (it has {', '.join(sorted(keys[name]))})",
                                    private,
                                )
                            )
    return problems


def check_copies(fix=None):
    example = settings.Tree(EXAMPLE, tracked_only=False)
    problems = []
    for label, tree, private in configs():
        if not private:
            continue
        for rel in tree.files():
            if rel.split("/", 1)[0] not in INVENTORY_DIR and tree.text(rel) == example.text(rel):
                problems.append(
                    found(
                        "copies",
                        f"{label}/{rel}",
                        1,
                        "equals config.example's, the default; delete it",
                        private,
                    )
                )
                if fix is not None and tree.rev is None:
                    fix.remove(CONFIG / rel)
    return problems


# A check that can fix takes a Fixes to collect its edits in.
FIXABLE = {"commented", "redundant", "copies"}
CHECKS = {
    "commented": check_commented,
    "redundant": check_redundant,
    "dead": check_dead,
    "doc-paths": check_doc_paths,
    "doc-vars": check_doc_vars,
    "doc-limits": check_doc_limits,
    "doc-anchors": check_doc_anchors,
    "doc-repeats": check_doc_repeats,
    "doc-gpu": check_doc_gpu,
    "duplicates": check_duplicates,
    "proxy-credentials": check_proxy_credentials,
    "broadcast": check_broadcast,
    "mapping-keys": check_mapping_keys,
    "shadow": check_shadow,
    "copies": check_copies,
    "retired": check_retired,
    "retired-paths": check_retired_paths,
    "removals": check_removals,
}


def main(argv):
    parser = cli.parser(__doc__)
    parser.add_argument(
        "--fix", action="store_true", help="write what the fixable checks can, then check again"
    )
    parser.add_argument(
        "checks", nargs="*", metavar="CHECK", help=f"some of: {', '.join(CHECKS)} (default: all)"
    )
    args = parser.parse_args(argv)
    unknown = [c for c in args.checks if c not in CHECKS]
    if unknown:
        parser.error(f"unknown check: {', '.join(unknown)}")
    return run(args.checks or list(CHECKS), Fixes() if args.fix else None)


def run(wanted, fix):
    """Run the WANTED checks; with FIX, apply what they can fix and run them again."""
    problems = [p for c in wanted for p in (CHECKS[c](fix) if c in FIXABLE else CHECKS[c]())]
    if fix is not None:
        for path in fix.apply():
            print(f"fixed {path}", file=sys.stderr)
        return run(wanted, None)
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
