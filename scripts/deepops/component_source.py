#!/usr/bin/env python3
"""What decides each component, and one digest of it per host: the "source" a deploy
records on the host (roles/component_record) and the one the checkouts would record
now. When they differ, a fix that is in the repository or the config never reached that
host (ComponentNotDeployed, rules/os-updates.yml; docs/deepops/component-state.md).

What decides a component, read from the tree, never listed apart:
  - its role (the one whose tasks record it present), and every role that role pulls in
    (meta dependencies, static include_role and import_role), to any depth;
  - the playbooks that apply the component's role, and the playbooks they import;
  - the in-tree plugins those files name (filters, actions, module_utils) and the
    repository files they name by path (scripts/deepops/component_expected.py), with the
    Python modules those import beside them;
  - per host, the config values of the settings those roles own or their code names
    (whichever role owns it), of the settings those values name in turn, and the config
    files those files and values name under inventory_dir; a setting read of other
    hosts (through hostvars) by every host's value.
component_record is left out of every other closure and has its own digest: every
component calls it, and a fix there would otherwise flag every component at once.
Files are compared by git blob id, so an unrelated commit moves no digest, and a working
tree with uncommitted changes hashes what is on disk.

  component_source.py [--config DIR] digest [--hosts H1,H2]
      {host: {component: digest}} as JSON, for the hosts named (default: all), and
      under "component_record" the record's own; the component_record role runs this
      once per run (it is cached for the trees as they are)
"""

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402
import settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RECORD = "component_record"
SELF = (RECORD,)  # roles with their own digest, left out of every other closure
INCLUDES = ("ansible.builtin.include_role", "include_role", "ansible.builtin.import_role", "import_role")
TASK_INCLUDES = (
    "ansible.builtin.include_tasks",
    "include_tasks",
    "ansible.builtin.import_tasks",
    "import_tasks",
)
PLAYBOOK_IMPORTS = ("ansible.builtin.import_playbook", "import_playbook")
DEPLOYS = ("playbooks/slurm-cluster.yml", "playbooks/k8s-cluster.yml")
SKIP_PARTS = ("molecule",)
FILTERS, ACTIONS, MODULE_UTILS = "plugins/filter", "plugins/action", "plugins/module_utils"
# A repository path named in a file: a script a task runs, an awk file it ships.
REPO_PATH = re.compile(r"\b((?:scripts|plugins)/[\w./-]+\.\w+)")
# A config file named through inventory_dir, in a default or a config value.
CONFIG_PATH = re.compile(r"inventory_dir\s*\}\}\s*/([\w./-]+)|inventory_dir\s*~\s*['\"]/([\w./-]+)['\"]")
DIGEST_LENGTH = 16
IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
# A comment line (YAML, shell, Python) or a Jinja comment: names there are not read.
COMMENT = re.compile(r"(?m)^[ \t]*#[^\n]*|\{#(?s:.*?)#\}")
# An expression that reads other hosts' settings: the names in it count for every host.
FLEET = re.compile(r"\{\{(?:(?!\}\}).)*\bhostvars\b(?:(?!\}\}).)*\}\}", re.S)
REGISTRY = "deepops_component_registry"
# Modules that change nothing on a host (a role of only these is inert).
INERT_MODULES = {
    f"{prefix}{name}"
    for prefix in ("", "ansible.builtin.")
    for name in ("assert", "fail", "debug", "set_fact", "meta", "include_vars")
}
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "deepops" / "component-sources"


def _load(text):
    try:
        return yaml.safe_load(text or "")
    except yaml.YAMLError:
        return None


def _tasks(items):
    for task in items if isinstance(items, list) else []:
        if isinstance(task, dict):
            for key in ("block", "rescue", "always"):
                yield from _tasks(task.get(key))
            yield task


def _role_of(rel):
    parts = PurePosixPath(rel).parts
    return parts[1] if len(parts) > 2 and parts[0] == "roles" and parts[1] != "galaxy" else None


def _wanted(rel):
    return not any(p in SKIP_PARTS for p in PurePosixPath(rel).parts)


def _static(name):
    return isinstance(name, str) and name and "{{" not in name and "{%" not in name


def records(tree):
    """[(component, file, the include's vars)] for each component_record call with a
    literal name in the roles' tasks."""
    out = []
    for rel in tree.files("roles"):
        parts = PurePosixPath(rel).parts
        if (
            "tasks" not in parts
            or not _wanted(rel)
            or rel.startswith("roles/galaxy/")
            or not rel.endswith((".yml", ".yaml"))
        ):
            continue
        for task in _tasks(_load(tree.text(rel))):
            include = next((task[k] for k in INCLUDES if isinstance(task.get(k), dict)), None)
            if not include or include.get("name") != RECORD:
                continue
            args = task.get("vars") or {}
            if _static(args.get("component_record_name")):
                out.append((str(args["component_record_name"]), rel, args))
    return out


def component_roles(tree):
    """{component: role}: the role whose tasks record it present. ValueError names a
    component two roles record."""
    found = {}
    for name, rel, args in records(tree):
        if args.get("component_record_action") != "present":
            continue
        role = _role_of(rel)
        if found.setdefault(name, role) != role:
            raise ValueError(f"{name}: recorded present by roles {found[name]} and {role}")
    return dict(sorted(found.items()))


class Closure:
    """What decides components, read once from TREE."""

    def __init__(self, tree):
        self.tree = tree
        self.files = tree.files()
        self.blobs = tree.blob_ids()
        self._texts, self._parsed, self._refs, self._direct_refs = {}, {}, {}, {}
        self._playbook_roles = None
        self._inert = {}
        self.filters = self._filter_names()
        self.actions = {
            PurePosixPath(p).stem for p in self.files if p.startswith(ACTIONS + "/") and p.endswith(".py")
        }

    def text(self, rel):
        if rel not in self._texts:
            self._texts[rel] = self.tree.text(rel) or ""
        return self._texts[rel]

    def parsed(self, rel):
        if rel not in self._parsed:
            self._parsed[rel] = _load(self.text(rel))
        return self._parsed[rel]

    def _filter_names(self):
        """{filter name: its plugin file}, from each file's filters() mapping."""
        out = {}
        for rel in self.files:
            if not (rel.startswith(FILTERS + "/") and rel.endswith(".py")):
                continue
            try:
                module = ast.parse(self.text(rel))
            except SyntaxError:
                continue
            for node in ast.walk(module):
                if isinstance(node, ast.FunctionDef) and node.name == "filters":
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Dict):
                            for key in sub.keys:
                                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                                    out[key.value] = rel
        return out

    def role_files(self, role):
        prefix = f"roles/{role}/"
        return [p for p in self.files if p.startswith(prefix) and _wanted(p)]

    def task_file(self, role, name):
        """ROLE's tasks file NAME (tasks_from), or None."""
        for suffix in ("", ".yml", ".yaml"):
            rel = f"roles/{role}/tasks/{name}{suffix}"
            if rel in self.blobs:
                return rel
        return None

    def meta_deps(self, role):
        meta = self.parsed(f"roles/{role}/meta/main.yml") or {}
        out = set()
        for dep in meta.get("dependencies") or [] if isinstance(meta, dict) else []:
            name = (
                dep
                if isinstance(dep, str)
                else (dep.get("role") or dep.get("name"))
                if isinstance(dep, dict)
                else None
            )
            if _static(name):
                out.add(name)
        return out

    def steps(self, rel):
        """What the tasks file REL runs next: [(role, tasks_from)] for its static role
        includes and imports, and the role's own tasks files it includes."""
        if rel in self._refs:
            return self._refs[rel]
        role = _role_of(rel)
        out = self._refs[rel] = []
        for task in _tasks(self.parsed(rel)):
            for key in INCLUDES:
                include = task.get(key)
                if isinstance(include, dict) and _static(include.get("name")):
                    tasks_from = include.get("tasks_from") or "main"
                    if _static(tasks_from):
                        out.append((include["name"], tasks_from))
            for key in TASK_INCLUDES:
                target = task.get(key)
                if isinstance(target, dict):
                    target = target.get("file")
                if _static(target) and role:
                    out.append((role, os.path.splitext(target)[0]))
        return out

    def reached(self, role):
        """{role: its tasks files that run} when ROLE runs whole: ROLE's main and every
        role and tasks file that pulls in, to any depth; SELF left out of others'. A role
        run by any entry runs its meta dependencies whole."""
        found, todo = {}, [(role, "main")]
        while todo:
            name, entry = todo.pop()
            if name in SELF and name != role:
                continue
            if name not in found:
                found[name] = set()
                todo += [(dep, "main") for dep in sorted(self.meta_deps(name))]
            rel = self.task_file(name, entry)
            if rel is None or rel in found[name]:
                continue
            found[name].add(rel)
            todo += self.steps(rel)
        return found

    def _applied(self):
        """{playbook: the role names its plays apply}, outside the two deploys."""
        if self._playbook_roles is None:
            self._playbook_roles = {}
            for rel in self.files:
                if not rel.startswith("playbooks/") or not rel.endswith((".yml", ".yaml")) or rel in DEPLOYS:
                    continue
                names = set()
                plays = self.parsed(rel)
                for play in plays if isinstance(plays, list) else []:
                    if not isinstance(play, dict):
                        continue
                    for entry in play.get("roles") or []:
                        if isinstance(entry, dict):
                            entry = entry.get("role") or entry.get("name")
                        names.add(entry)
                    for key in ("pre_tasks", "tasks", "post_tasks", "handlers"):
                        for task in _tasks(play.get(key)):
                            for inc in INCLUDES:
                                if isinstance(task.get(inc), dict):
                                    names.add(task[inc].get("name"))
                self._playbook_roles[rel] = {n for n in names if _static(n)}
        return self._playbook_roles

    def playbooks(self, roles):
        """The playbooks (outside the two deploys) that apply one of ROLES, and what they
        import."""
        found = {rel for rel, names in self._applied().items() if names & roles}
        todo = sorted(found)
        while todo:
            rel = todo.pop()
            plays = self.parsed(rel)
            for play in plays if isinstance(plays, list) else []:
                for key in PLAYBOOK_IMPORTS:
                    target = play.get(key) if isinstance(play, dict) else None
                    if _static(target):
                        path = os.path.normpath((PurePosixPath(rel).parent / target).as_posix())
                        if path in self.blobs and path not in found:
                            found.add(path)
                            todo.append(path)
        return found

    def _direct(self, rel):
        """The plugins, repository files and sibling modules REL names itself."""
        if rel in self._direct_refs:
            return self._direct_refs[rel]
        text, more = self.text(rel), set()
        if rel.endswith((".yml", ".yaml", ".j2")) or "/templates/" in rel:
            for name in re.findall(r"\|\s*(\w+)", text) + re.findall(
                r"\b(?:map|select|reject)\(\s*['\"](\w+)['\"]", text
            ):
                if name in self.filters:
                    more.add(self.filters[name])
            for name in re.findall(r"(?m)^[\s-]*(\w+)\s*:", text):
                if name in self.actions:
                    more.add(f"{ACTIONS}/{name}.py")
        if rel.endswith((".yml", ".yaml", ".j2")):
            code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("#", "{#")))
            more |= {p for p in REPO_PATH.findall(code) if not p.startswith("plugins/callback/")}
        if rel.endswith(".py"):
            for name in re.findall(r"module_utils[./](\w+)", text):
                more.add(f"{MODULE_UTILS}/{name}.py")
            here = PurePosixPath(rel).parent
            for a, b in re.findall(r"(?m)^\s*(?:from\s+(\w+)\s+import|import\s+(\w+))", text):
                more.add((here / f"{a or b}.py").as_posix())
        self._direct_refs[rel] = {p for p in more if p in self.blobs and p != rel}
        return self._direct_refs[rel]

    def named(self, files):
        """The plugins and repository files FILES name, and the modules those import, to
        any depth."""
        found, todo = set(), sorted(files)
        while todo:
            for path in self._direct(todo.pop()):
                if path not in found and path not in files:
                    found.add(path)
                    todo.append(path)
        return found

    def inert(self, role):
        """Whether ROLE changes nothing on a host: only tasks that check, say or set
        facts (retired_settings), no templates, files, handlers or modules."""
        if role not in self._inert:
            ok = True
            for rel in self.role_files(role):
                part = PurePosixPath(rel).parts[2]
                if part in ("defaults", "vars", "meta") or not rel.endswith((".yml", ".yaml", ".md")):
                    ok = ok and part in ("defaults", "vars", "meta")
                    continue
                if part != "tasks":
                    ok = False
                    continue
                for task in _tasks(self.parsed(rel)):
                    if any(k in task for k in ("block", "rescue", "always")):
                        continue
                    if not any(k in INERT_MODULES for k in task):
                        ok = False
            self._inert[role] = ok and bool(self.role_files(role))
        return self._inert[role]

    def entries(self, role):
        """{name: (file, value)} of ROLE's defaults and vars."""
        out = {}
        for rel in self.role_files(role):
            if PurePosixPath(rel).parts[2] in ("defaults", "vars") and rel.endswith((".yml", ".yaml")):
                data = self.parsed(rel)
                for name, value in (data or {}).items() if isinstance(data, dict) else ():
                    out[str(name)] = (rel, value)
        return out

    def code(self, component, role):
        """What decides COMPONENT, recorded by ROLE: (the roles, the lines to hash, the
        names its code reads, the texts that may name config files).

        ROLE's own files count whole. A role it pulls in counts by what of it runs: the
        tasks files reached, its handlers, the templates, files and modules those name,
        and, of its defaults and vars, only the entries that code reads, to any depth (a
        shared role of paths or defaults changes many times a day, almost never for this
        component); an inert role not at all. Of the component map
        (deepops_component_registry), only the entries the code names."""
        reached = self.reached(role)
        roles = {r for r in reached if r == role or not self.inert(r)}
        files = set(self.role_files(role))
        for r in sorted(roles - {role}):
            run = set(reached[r])
            run |= {p for p in self.role_files(r) if PurePosixPath(p).parts[2] == "handlers"}
            said = "\n".join(self.text(p) for p in sorted(run))
            for p in self.role_files(r):
                part = PurePosixPath(p).parts
                if part[2] in ("templates", "files") and ("/".join(part[3:]) in said or part[-1] in said):
                    run.add(p)
                elif part[2] == "library" and re.search(
                    rf"(?m)^[\s-]*{re.escape(PurePosixPath(p).stem)}\s*:", said
                ):
                    run.add(p)
            files |= run
        files |= self.playbooks({role})
        if any(not self.role_files(r) for r in roles) and "roles/requirements.yml" in self.blobs:
            files.add("roles/requirements.yml")  # a Galaxy role: pinned there
        files |= self.named(files)
        texts = [self.text(p) for p in sorted(files)]
        code = "\n".join(self.text(p) for p in sorted(files) if PurePosixPath(p).parts[2:3] != ("meta",))
        names = set(IDENTIFIER.findall(code))
        quoted = set(re.findall(r"['\"](\w+)['\"]", code)) | {component}
        entries = {}
        for r in sorted(roles - {role}):
            for name, item in self.entries(r).items():
                entries.setdefault(name, item)
        taken, todo = {}, sorted(names & set(entries))
        while todo:
            name = todo.pop()
            if name in taken:
                continue
            rel, value = entries[name]
            if name == REGISTRY and isinstance(value, dict):
                value = {k: v for k, v in value.items() if k in quoted}
            taken[name] = (rel, value)
            dumped = json.dumps(value, sort_keys=True, default=str)
            texts.append(dumped)
            for more in set(IDENTIFIER.findall(dumped)) - names:
                names.add(more)
                if more in entries:
                    todo.append(more)
        lines = [f"{p} {self.blobs[p]}" for p in sorted(files)]
        for name, (rel, value) in sorted(taken.items()):
            dumped = json.dumps(value, sort_keys=True, default=str)
            lines.append(f"{rel}:{name} {hashlib.sha256(dumped.encode()).hexdigest()}")
        return roles, lines, names, texts


def config_files(texts):
    """The config paths (relative to the config directory) TEXTS name via inventory_dir."""
    out = set()
    for text in texts:
        for a, b in CONFIG_PATH.findall(text):
            out.add((a or b).strip("/"))
    return out


def _value(value):
    if isinstance(value, settings.Vault):
        return {"vault": value.ciphertext}
    if value is settings.MISSING or value is settings.AMBIGUOUS:
        return repr(value)
    return settings.plain(value)


_HASHED = object()  # a setting a host leaves at a default its files already count


class Sources:
    """Each component's digest per host, for one deepops tree and one config."""

    def __init__(self, tree, config, owner_map):
        self.closure = Closure(tree)
        self.config, self.owner_map = config, owner_map
        self.components = component_roles(tree)
        self.components[RECORD] = RECORD
        self._code = {}
        self._config_blobs = {}
        self._resolved = {}
        self._owner_roles = {n: o.roles for n, o in owner_map.items()}

    def code(self, component):
        if component not in self._code:
            role = self.components[component]
            roles, lines, names, texts = self.closure.code(component, role)
            # the settings it reads: every one its own role owns, and of the roles it
            # pulls in, those its code names
            owned = {n for n, r in self._owner_roles.items() if role in r or (r & roles and n in names)}
            # and those of roles it never runs that its code (not a comment) names: their
            # defaults never reach it, only a config value does (the roster's people,
            # another role's limits); component_record's are its own digest's
            live = set(IDENTIFIER.findall(COMMENT.sub("", "\n".join(texts))))
            outside = {
                n for n, r in self._owner_roles.items()
                if n in live and n not in owned and not r & (roles | set(SELF))
            }  # fmt: skip
            # and of those, the ones it reads of every host (hostvars): each host's value
            fleet = {n for expr in FLEET.findall("\n".join(texts)) for n in IDENTIFIER.findall(expr)}
            self._code[component] = (
                lines,
                owned | outside,
                outside,
                fleet & (owned | outside),
                config_files(texts),
            )
        return self._code[component]

    def resolved(self, host):
        if host not in self._resolved:
            self._resolved[host] = self.config.resolve(host, self.owner_map)
        return self._resolved[host]

    def config_blobs(self, rel):
        if rel not in self._config_blobs:
            self._config_blobs[rel] = sorted(self.config.tree.blob_ids(rel).items())
        return self._config_blobs[rel]

    def _site_value(self, name, resolved):
        """NAME's value as RESOLVED gives it, or _HASHED for one not here or a default already hashed."""
        if name not in resolved:
            return _HASHED
        value, where = resolved[name]
        owner = self.owner_map.get(name)
        if owner is not None and where in owner.paths:
            return _HASHED  # the default: in the role's own files above, or never there
        return value

    def digest(self, host, component, resolved=None):
        lines, owned, _, fleet, paths = self.code(component)
        resolved = resolved if resolved is not None else self.resolved(host)
        values = {}
        # A value that is a template reads the settings it names (people, from the
        # roster's file through people_roster): those count too, to any depth.
        todo = sorted(owned, reverse=True)
        while todo:
            name = todo.pop()
            if name in values:
                continue
            if name in fleet:
                each = {h: self._site_value(name, self.resolved(h)) for h in sorted(self.config.hosts)}
                found = [v for v in each.values() if v is not _HASHED]
                if not found:
                    continue
                values[name] = {h: _value(v) for h, v in each.items() if v is not _HASHED}
            else:
                value = self._site_value(name, resolved)
                if value is _HASHED:
                    continue
                values[name] = _value(value)
                found = [value]
            for value in found:
                if isinstance(value, str):
                    paths = paths | config_files([value])
                    todo.extend(n for n in IDENTIFIER.findall(value) if n in resolved and n not in values)
        h = hashlib.sha256()
        h.update("\n".join(lines).encode())
        h.update(b"\nconfig\n")
        h.update(json.dumps(values, sort_keys=True, default=repr).encode())
        for rel in sorted(paths):
            for path, blob in self.config_blobs(rel):
                h.update(f"\n{path} {blob}".encode())
        return h.hexdigest()[:DIGEST_LENGTH]

    def all(self, hosts=None):
        """{host: {component: digest}} for HOSTS (default: the inventory's)."""
        out = {}
        for host in sorted(hosts if hosts is not None else self.config.hosts):
            out[host] = {c: self.digest(host, c) for c in self.components}
        return out


def sources(config_dir, rev=None, config_rev=None, hosts=None):
    """Sources.all() for the deepops tree at REV and the config at CONFIG_REV (None: as
    they are on disk)."""
    tree = settings.Tree(ROOT, rev=rev)
    config = settings.Config(settings.site_tree(config_dir, config_rev))
    return Sources(tree, config, settings.owners(tree, private=True)).all(hosts)


def _state_key(config_dir):
    """A key for the trees as they are: both HEADs and their uncommitted changes; None
    when either cannot be read (then nothing is cached)."""
    h = hashlib.sha256()
    for repo in (ROOT, Path(config_dir)):
        for args in (["rev-parse", "HEAD"], ["diff", "HEAD", "--binary"], ["status", "--porcelain", "-z"]):
            out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=settings.CLEAN_ENV)
            if out.returncode != 0:
                return None
            h.update(out.stdout)
        # untracked config files (vaulted secrets are often kept so): their content
        for rel in filter(None, out.stdout.split(b"\0")):
            if rel.startswith(b"?? "):
                path = repo / os.fsdecode(rel[3:])
                if path.is_file():
                    h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def cached(config_dir, hosts):
    """sources() for the working trees, through a per-state cache: a deploy asks once per
    component, and the trees do not move under it (scripts/deepops/deploy_lock.py)."""
    key = _state_key(config_dir)
    path = CACHE / f"{key}.json" if key else None
    data = None
    if path and path.is_file():
        try:
            data = json.loads(path.read_text())
        except ValueError:
            data = None
    if data is None or any(h not in data for h in hosts):
        data = sources(config_dir)
        if path:
            CACHE.mkdir(parents=True, exist_ok=True)
            for old in CACHE.glob("*.json"):
                old.unlink(missing_ok=True)
            fd, tmp = tempfile.mkstemp(dir=CACHE, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp, path)
    return {h: data.get(h, {}) for h in hosts}


def main(argv):
    parser = cli.parser(__doc__)
    parser.add_argument("--config", default=os.environ.get("DEEPOPS_CONFIG_DIR", str(ROOT / "config")))
    sub = parser.add_subparsers(dest="command", required=True)
    dg = sub.add_parser("digest", help="each host's component digests, as JSON")
    dg.add_argument("--hosts", help="comma-separated (default: every inventory host)")
    args = parser.parse_args(argv)
    if args.hosts:
        out = cached(args.config, [h for h in args.hosts.split(",") if h])
    else:
        out = sources(args.config)
    json.dump(out, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
