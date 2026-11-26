#!/usr/bin/env python3
"""What each setting resolves to on each host: the role default that owns it and the
config that overrides it. One model for config_lint and the deploy preview.

    settings.py diff [--from REV] [--config-from REV]   what a deploy would change, per host
    settings.py show HOST                               every setting HOST resolves, and where from
    settings.py show HOST NAME...                       those settings' values alone, one a line

A role's defaults own a setting's default (ours, a Galaxy role's or Kubespray's), and a
config directory (config/ or config.example/) states only what differs. A host resolves
a setting the way Ansible does for inventory variables: the owning default, then the
inventory file's vars of all and of each group, group_vars/all, group_vars of each
group (by depth, priority and name), the inventory file's host vars, host_vars. Play
vars, role vars and set_fact are not settings and are not modelled.

`diff` resolves every host twice, once with the role defaults and config it was last
deployed from and once with the working trees, and prints each value that changes; a
setting that only gained a default (it was unset) is listed apart. Each host's start is
its own record (roles/deploy_record writes ansible_local.deepops_deploy at the end of a
deploy), read from the controller's fact cache; --from and --config-from set one start
for every host instead. Nothing is decrypted (a vaulted value is compared as its
ciphertext) and nothing connects to a host.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
KUBESPRAY = "submodules/kubespray"
# What `git` in a commit hook inherits points at this repository's index; a child
# repository (config/, the Kubespray submodule) must not see it.
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


class Tree:
    """A directory's files, from the working tree or from a git revision.

    rev=None reads the working tree: tracked files when `tracked_only` (a commit holds
    nothing else), every file otherwise. A revision reads that commit's blobs.
    """

    def __init__(self, root, rev=None, tracked_only=True):
        self.root, self.rev, self.tracked_only = Path(root), rev, tracked_only

    def _git(self, *args, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            errors="surrogateescape",
            env=CLEAN_ENV,
            check=check,
        )

    def files(self, prefix=""):
        """Relative paths of the files under PREFIX."""
        if self.rev:
            out = self._git("ls-tree", "-r", "-z", "--name-only", self.rev, "--", prefix or ".").stdout
            return sorted(filter(None, out.split("\0")))
        if self.tracked_only:
            out = self._git("ls-files", "-z", "--", prefix or ".").stdout
            return sorted(p for p in filter(None, out.split("\0")) if (self.root / p).is_file())
        base = self.root / prefix
        if base.is_file():
            return [prefix]
        if not base.is_dir():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file())

    def blob_ids(self, prefix=""):
        """{path: git blob id} of the files under PREFIX: the commit's at a revision, the
        content on disk otherwise (the id git would give it), so the two agree whenever
        the content does."""
        if self.rev:
            out = self._git("ls-tree", "-r", "-z", self.rev, "--", prefix or ".").stdout
            found = {}
            for record in filter(None, out.split("\0")):
                meta, path = record.split("\t", 1)
                kind, ident = meta.split()[1:3]
                if kind == "blob":
                    found[path] = ident
            return found
        found = {}
        for rel in self.files(prefix):
            path = self.root / rel
            data = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
            found[rel] = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        return found

    def text(self, rel):
        """REL's text, or None when it does not exist."""
        if self.rev:
            out = self._git("show", f"{self.rev}:{rel}", check=False)
            return out.stdout if out.returncode == 0 else None
        path = self.root / rel
        return path.read_text(errors="ignore") if path.is_file() else None

    def entries(self, sub):
        """{name: is a directory} directly under SUB (an empty directory included, on disk)."""
        if self.rev:
            out = self._git("ls-tree", "-z", self.rev, f"{sub}/", check=False).stdout
            found = {}
            for record in filter(None, out.split("\0")):
                meta, path = record.split("\t", 1)
                found[path.split("/", 1)[1]] = meta.split()[1] == "tree"
            return found
        base = self.root / sub
        return {p.name: p.is_dir() for p in base.iterdir()} if base.is_dir() else {}

    def submodule(self, path):
        """The tree of the submodule at PATH, at the commit this tree records for it."""
        if not self.rev:
            return Tree(self.root / path, tracked_only=self.tracked_only)
        out = self._git("ls-tree", self.rev, "--", path).stdout.split()
        return Tree(self.root / path, rev=out[2] if len(out) > 2 else None)


# Values ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Vault:
    """A vaulted value: equal only to the same ciphertext, never decrypted."""

    ciphertext: str

    def __repr__(self):
        return "<vault>"


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("!vault", lambda loader, node: Vault(loader.construct_scalar(node)))
_Loader.add_constructor("!unsafe", lambda loader, node: loader.construct_scalar(node))


def load(text):
    """YAML as Ansible stores variables: a vaulted value stays opaque. None on an error
    or a vault-encrypted file."""
    if text is None or text.lstrip().startswith("$ANSIBLE_VAULT"):
        return None
    try:
        return yaml.load(text, Loader=_Loader)
    except yaml.YAMLError:
        return None


def duplicate_keys(text):
    """(line, key) for each key that appears twice in one mapping of TEXT, its later
    occurrences: Ansible keeps the last and only warns, so the first is silently lost.
    Nothing for a vault-encrypted file or text that is not YAML."""
    if text is None or text.lstrip().startswith("$ANSIBLE_VAULT"):
        return []
    try:
        nodes = list(yaml.compose_all(text, Loader=_Loader))
    except yaml.YAMLError:
        return []
    found, stack = [], [n for n in nodes if n is not None]
    while stack:
        node = stack.pop()
        if isinstance(node, yaml.MappingNode):
            seen = set()
            for key, value in node.value:
                if isinstance(key, yaml.ScalarNode) and key.value != "<<":
                    if key.value in seen:
                        found.append((key.start_mark.line + 1, key.value))
                    seen.add(key.value)
                stack.append(value)
        elif isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)
    return sorted(found)


def plain(value):
    """VALUE with Ansible's tagged str/int/... subclasses turned back into Python's own."""
    if isinstance(value, dict):
        return {plain(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    for kind in (bool, int, float, str):
        if isinstance(value, kind):
            return kind(value)
    return value


def dump(value, block=False):
    """VALUE as YAML: collections inline (for a message), or in block style (for a file)."""
    text = yaml.safe_dump(value, default_flow_style=not block, width=float("inf"), sort_keys=False)
    return text.removesuffix("...\n").strip()


class _Missing:
    def __repr__(self):
        return "<unset>"


MISSING = _Missing()  # no default and no config value
AMBIGUOUS = type("Ambiguous", (), {"__repr__": lambda self: "<roles disagree>"})()
OVERRIDDEN = type("Overridden", (), {"__repr__": lambda self: "<overridden>"})()


# Owners ------------------------------------------------------------------------------


@dataclass
class Owner:
    """The role defaults that define a setting; value is AMBIGUOUS when they disagree."""

    paths: list
    value: object

    @property
    def roles(self):
        """The owning roles by the name a play lists them under (kubernetes/client)."""
        out = set()
        for path in self.paths:
            parts = PurePosixPath(path).parts
            start = 2 if path.startswith(("roles/galaxy/", f"{KUBESPRAY}/")) else 1
            start += 1 if path.startswith(f"{KUBESPRAY}/") else 0
            out.add("/".join(parts[start : parts.index("defaults")]))
        return out


# Settings our plays read although the upstream role whose defaults define them is never
# loaded there: for our code they have no default, so config sets them.
NOT_OURS = {
    # Kubespray's kubernetes/client role; playbooks/k8s-cluster.yml fetches kubectl and
    # admin.conf from where that role puts them.
    "artifacts_dir",
}


def _defaults_files(tree, under):
    """The role defaults files under UNDER: defaults/main.y*ml and defaults/main/*.y*ml."""
    for rel in tree.files(under):
        parts = PurePosixPath(rel).parts
        if "defaults" not in parts or "molecule" in parts or not rel.endswith((".yml", ".yaml")):
            continue
        tail = parts[parts.index("defaults") + 1 :]
        if (len(tail) == 1 and tail[0] in ("main.yml", "main.yaml")) or (
            len(tail) == 2 and tail[0] == "main"
        ):
            yield rel


def owners(tree=None, private=False):
    """{name: Owner} from every role's defaults: ours and Galaxy's under roles/, and
    Kubespray's (at the commit TREE records for the submodule). A name starting with
    an underscore is a role's private helper, never a setting: PRIVATE keeps them too,
    for rendering a role's own expressions (component_pins.py)."""
    tree = tree or Tree(ROOT)
    found = {}
    # Galaxy roles are installed, never committed: read from disk at any revision.
    galaxy = Tree(ROOT / "roles" / "galaxy", tracked_only=False)
    sources = [(tree, "roles", ""), (galaxy, "", "roles/galaxy/")]
    kubespray = tree.submodule(KUBESPRAY)
    if (kubespray.root / ".git").exists():
        sources.append((kubespray, "roles", f"{KUBESPRAY}/"))
    for src, under, prefix in sources:
        for rel in _defaults_files(src, under):
            if src is tree and rel.startswith("roles/galaxy/"):
                continue
            data = load(src.text(rel))
            if not isinstance(data, dict):
                continue
            where = prefix + rel
            for name, value in data.items():
                if isinstance(name, str) and (private or not name.startswith("_")) and name not in NOT_OURS:
                    found.setdefault(name, []).append((where, value))
    result = {}
    for name, defs in found.items():
        values = [v for _, v in defs]
        same = all(v == values[0] for v in values)
        result[name] = Owner([p for p, _ in defs], values[0] if same else AMBIGUOUS)
    return result


# Variable files ----------------------------------------------------------------------


@dataclass
class Entry:
    """One setting a variable file sets (active) or shows commented ("#name: value")."""

    name: str
    line: int  # 1-based line of "name:"
    end: int  # last line of its value
    active: bool
    value: object
    pinned: bool  # the comment line right above starts with "# site:"
    example: bool  # commented, and its first line ends in "# example"


EXAMPLE_MARK = re.compile(r"\s#\s*example\s*$")


def _is_continuation(line, active):
    """Whether LINE continues the value of the setting above it."""
    if active:
        return line.startswith((" ", "\t", "-")) or line == ""
    if not line.startswith("#"):
        return False
    return line[1:].startswith(("  ", "\t", "- "))


def entries(text):
    """The settings in a group_vars or host_vars file, in order.

    An active setting is a top-level key; a commented one is "#name:" (no space after
    the #) and the "#  ..." lines below it. "# text", with one space, is prose. A
    commented setting shows its owner's default unless its first line ends in
    "# example": then it shows a value a site might set.
    """
    lines = text.splitlines()
    out, i = [], 0
    data = load(text)
    data = data if isinstance(data, dict) else {}
    while i < len(lines):
        line = lines[i]
        active = not line.startswith(("#", " ", "\t", "-")) and ":" in line
        commented = line.startswith("#") and not line.startswith(("# ", "##")) and ":" in line
        head = line if active else line[1:]
        name = head.split(":", 1)[0]
        if not (active or commented) or not name.isidentifier() or name != name.strip():
            i += 1
            continue
        end = i
        while end + 1 < len(lines) and _is_continuation(lines[end + 1], active):
            end += 1
        while end > i and lines[end].strip() in ("", "#"):
            end -= 1
        if active:
            value = data.get(name, MISSING)
        else:
            block = load("".join(raw[1:] + "\n" for raw in lines[i : end + 1]))
            value = block.get(name, MISSING) if isinstance(block, dict) else MISSING
        pinned = i > 0 and lines[i - 1].startswith("# site:")
        example = not active and EXAMPLE_MARK.search(line) is not None
        out.append(Entry(name, i + 1, end + 1, active, value, pinned, example))
        i = end + 1
    return out


# Config directories ------------------------------------------------------------------


@dataclass
class Source:
    """A variable file of a config directory and the hosts it applies to."""

    rel: str  # e.g. group_vars/all.yml, host_vars/gpu01
    kind: str  # "group" or "host"
    entity: str  # the group or host name
    entries: list


class Config:
    """A config directory (config/ or config.example/): its inventory, and the
    group_vars and host_vars files, read from TREE (a working tree or a revision)."""

    SUFFIXES = ("", ".yml", ".yaml", ".json")

    def __init__(self, tree, inventory="inventory"):
        self.tree = tree
        self.sources = []
        for kind, sub in (("group", "group_vars"), ("host", "host_vars")):
            for rel in tree.files(sub):
                parts = PurePosixPath(rel).parts
                if len(parts) < 2 or not rel.endswith(self.SUFFIXES):
                    continue
                entity = parts[1] if len(parts) > 2 else self._stem(parts[1])
                if entity is None:
                    continue
                text = tree.text(rel) or ""
                self.sources.append(Source(rel, kind, entity, entries(text)))
        self._load_inventory(tree.text(inventory))

    @classmethod
    def _stem(cls, name):
        for suffix in cls.SUFFIXES[1:]:
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return name

    def _load_inventory(self, text):
        """Hosts, each host's groups in Ansible's order, and the inventory file's own vars."""
        self.hosts, self.group_hosts, self.inv_group_vars, self.inv_host_vars = {}, {}, {}, {}
        if text is None:
            return
        # The repository's ansible.cfg, wherever this runs from: it keeps group names such
        # as slurm-node as written (force_valid_group_names). An inventory that does not
        # parse is an error here, never an empty one.
        os.environ.setdefault("ANSIBLE_CONFIG", str(ROOT / "ansible.cfg"))
        os.environ["ANSIBLE_INVENTORY_UNPARSED_FAILED"] = "true"
        from ansible.inventory.helpers import sort_groups
        from ansible.inventory.manager import InventoryManager
        from ansible.parsing.dataloader import DataLoader
        from ansible.utils.display import Display

        Display().verbosity = -1
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory"
            path.write_text(text)
            inv = InventoryManager(loader=DataLoader(), sources=[str(path)])
            for host in inv.get_hosts():
                groups = sort_groups(host.get_groups())
                self.hosts[host.name] = [g.name for g in groups]
                # The parser's own inventory_file/inventory_dir name the temporary copy.
                self.inv_host_vars[host.name] = {
                    k: plain(v) for k, v in host.vars.items() if k not in ("inventory_file", "inventory_dir")
                }
                for g in groups:
                    self.group_hosts.setdefault(g.name, []).append(host.name)
                    self.inv_group_vars[g.name] = {k: plain(v) for k, v in g.vars.items()}

    def chain(self, host):
        """HOST's variable sources, lowest precedence first: (label, {name: Entry or value})."""
        # A group no inventory host is in is resolved for a host of only that group.
        groups = self.hosts.get(host) or (["all", host[1:]] if host.startswith("@") else ["all"])
        by_entity = {}
        for src in self.sources:
            by_entity.setdefault((src.kind, src.entity), []).append(src)
        out = []
        for g in ["all"] + [g for g in groups if g != "all"]:
            if self.inv_group_vars.get(g):
                out.append((f"inventory [{g}:vars]", self.inv_group_vars[g]))
        for g in ["all"] + [g for g in groups if g != "all"]:
            for src in by_entity.get(("group", g), []):
                out.append((src.rel, {e.name: e for e in src.entries if e.active}))
        if self.inv_host_vars.get(host):
            out.append((f"inventory {host}", self.inv_host_vars[host]))
        for src in by_entity.get(("host", host), []):
            out.append((src.rel, {e.name: e for e in src.entries if e.active}))
        return out

    def hosts_of(self, src):
        """The hosts SRC applies to; a group without inventory hosts stands for one
        host of its own, "@group", so its lines are still judged."""
        if src.kind == "host":
            return [src.entity]
        found = sorted(self.hosts) if src.entity == "all" else self.group_hosts.get(src.entity, [])
        return found or [f"@{src.entity}"]

    def resolve(self, host, owner_map):
        """{name: (value, where)} for every setting HOST resolves."""
        values = {n: (o.value, o.paths[0]) for n, o in owner_map.items()}
        for label, settings in self.chain(host):
            for name, item in settings.items():
                values[name] = (item.value if isinstance(item, Entry) else item, label)
        return values

    def below(self, host, src, name, owner_map):
        """What HOST would get for NAME without SRC's line, or OVERRIDDEN when SRC does
        not decide it there (a more specific source sets it too)."""
        chain = self.chain(host)
        labels = [label for label, settings in chain if name in settings]
        if not labels or labels[-1] != src.rel:
            return OVERRIDDEN
        lower = MISSING
        owner = owner_map.get(name)
        if owner is not None:
            lower = owner.value
        for label, settings in chain:
            if label == src.rel:
                break
            if name in settings:
                item = settings[name]
                lower = item.value if isinstance(item, Entry) else item
        return lower


def redundant(config, owner_map):
    """(Source, Entry) for each active setting equal to what every host it decides would
    get without it, and (Source, Entry) for one no host takes (always overridden)."""
    same, unused = [], []
    for src in config.sources:
        hosts = config.hosts_of(src)
        for e in (e for e in src.entries if e.active):
            if isinstance(e.value, Vault) or e.value is MISSING:
                continue
            lows = [config.below(h, src, e.name, owner_map) for h in hosts]
            deciding = [low for low in lows if low is not OVERRIDDEN]
            if not deciding:
                unused.append((src, e))
            elif all(low is not AMBIGUOUS and low is not MISSING and low == e.value for low in deciding):
                same.append((src, e))
    return same, unused


# Command line ------------------------------------------------------------------------


def site_tree(config_dir, rev=None):
    return Tree(config_dir, rev=rev, tracked_only=False)


def site_rev(config_dir):
    """The revision of the site config CONFIG_DIR a check reads, or None for its working
    tree. The site's own commit hook (scripts/deepops/config-pre-commit.sh) passes what is
    being committed (DEEPOPS_SITE_REV, a tree); deepops' commit hook (pre-commit sets
    PRE_COMMIT) reads the site as committed (HEAD), when it has a commit: one session's
    half-done edit there must not block every other's commit."""
    if os.environ.get("DEEPOPS_SITE_REV"):
        return os.environ["DEEPOPS_SITE_REV"]
    if not (os.environ.get("PRE_COMMIT") and (Path(config_dir) / ".git").exists()):
        return None
    head = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", "HEAD"], cwd=config_dir, capture_output=True, env=CLEAN_ENV
    )
    return "HEAD" if head.returncode == 0 else None


def changes(old, old_owners, new, new_owners, hosts=None):
    """{host: [(name, before, after, where)]} for every value that differs between two
    (Config, owners) states, for HOSTS (default: every host either state has)."""
    out = {}
    for host in sorted(hosts if hosts is not None else set(old.hosts) | set(new.hosts)):
        before, after = old.resolve(host, old_owners), new.resolve(host, new_owners)
        rows = []
        for name in sorted(set(before) | set(after)):
            (b, bw), (a, aw) = before.get(name, (MISSING, "")), after.get(name, (MISSING, ""))
            if b != a:
                rows.append((name, b, a, aw or bw))
        if rows:
            out[host] = rows
    return out


def deployed(config_dir, hosts):
    """{host: record}: each host's latest deploy record (ansible_local.deepops_deploy),
    read through Ansible from the controller's fact cache. An ad-hoc debug task runs on
    the controller, so nothing connects to a host; a host without a fresh cached record
    is left out."""
    ansible = Path(sys.executable).with_name("ansible")
    env = dict(CLEAN_ENV, ANSIBLE_LOAD_CALLBACK_PLUGINS="1", ANSIBLE_STDOUT_CALLBACK="ansible.builtin.json")
    out = subprocess.run(
        [str(ansible) if ansible.exists() else "ansible", "-i", str(Path(config_dir) / "inventory"),
         ":".join(hosts), "-m", "ansible.builtin.debug", "-a", "var=ansible_local.deepops_deploy"],
        cwd=ROOT, capture_output=True, text=True, errors="replace", env=env,
    )  # fmt: skip
    try:
        results = json.loads(out.stdout)["plays"][0]["tasks"][0]["hosts"]
    except (ValueError, KeyError, IndexError):
        return {}
    found = {}
    for host, result in results.items():
        records = result.get("ansible_local.deepops_deploy")
        if isinstance(records, dict) and records:
            starts = [start(r) for r in records.values() if isinstance(r, dict)]
            starts = [r for r in starts if r]
            if starts:
                found[host] = max(starts, key=lambda r: r.get("time", ""))
    return found


def start(deploy):
    """Where a deploy's diff starts: its last full run (every tag), else its last partial
    one (roles/deploy_record keeps both; a flat record is the earlier layout's full run)."""
    if "deepops" in deploy:
        return deploy
    return deploy.get("full") or deploy.get("partial")


def cmd_diff(args):
    config_dir = Path(args.config)
    new = Config(site_tree(config_dir))
    new_owners = owners(Tree(ROOT))
    starts = {}  # (deepops rev, config rev) -> hosts
    if args.from_rev:
        starts[(args.from_rev, args.config_from)] = sorted(new.hosts)
    else:
        records = deployed(config_dir, sorted(new.hosts))
        for host in sorted(new.hosts):
            if host in records:
                r = records[host]
                starts.setdefault((r["deepops"], r["config"]), []).append(host)
                notes = [
                    f"{tree} had uncommitted changes"
                    for tree in ("deepops", "config")
                    if r.get(f"{tree}_dirty")
                ]
                if r.get("tags") not in (["all"], None):
                    notes.append(f"no full run recorded, only one of tags {', '.join(r.get('tags') or [])}")
                if notes:
                    print(
                        f"{host}: last deployed {r['time']}: "
                        + "; ".join(notes)
                        + ", so the start is approximate"
                    )
        missing = sorted(set(new.hosts) - set(records))
        if missing:
            print(
                f"no deploy record cached for {', '.join(missing)}: they were never deployed with one, or the "
                "cache expired. Refresh it (this connects): uv run ansible "
                f"{':'.join(missing)} -m ansible.builtin.setup -a filter=ansible_local; or pass --from REV."
            )
    found = {}
    for (rev, config_rev), hosts in starts.items():
        old = Config(site_tree(config_dir, config_rev or None))
        found.update(changes(old, owners(Tree(ROOT, rev=rev)), new, new_owners, hosts))
    # Hosts that change alike print once, together.
    groups = {}
    for host, rows in sorted(found.items()):
        groups.setdefault(repr(rows), (rows, []))[1].append(host)
    for rows, hosts in groups.values():
        print(f"{', '.join(hosts)}:")
        for name, before, after, where in (row for row in rows if row[1] is not MISSING):
            print(f"  {name}: {show(before)} -> {show(after)}  ({where})")
        added = [name for name, before, *_ in rows if before is MISSING]
        if added:
            print(f"  newly defined (they were unset): {', '.join(added)}")
    if starts and not found:
        print("no setting changes on any host")
    return 0


def show(value):
    return repr(value) if value is MISSING or value is AMBIGUOUS or isinstance(value, Vault) else dump(value)


def cmd_show(args):
    config = Config(site_tree(Path(args.config)))
    if args.host not in config.hosts:
        print(f"{args.host} is not in {args.config}/inventory", file=sys.stderr)
        return 2
    resolved = config.resolve(args.host, owners())
    if args.names:  # for a script: the value alone, and an unset name fails
        unset = [name for name in args.names if name not in resolved or resolved[name][0] is MISSING]
        if unset:
            print(f"not set for {args.host}: {', '.join(unset)}", file=sys.stderr)
            return 1
        for name in args.names:
            print(show(resolved[name][0]))
        return 0
    for name, (value, where) in sorted(resolved.items()):
        print(f"{name}: {show(value)}  ({where})")
    return 0


def main(argv=None):
    parser = cli.parser(__doc__)
    parser.add_argument("--config", default=os.environ.get("DEEPOPS_CONFIG_DIR", str(ROOT / "config")))
    sub = parser.add_subparsers(dest="command", required=True)
    diff = sub.add_parser("diff", help="per-host setting changes since a revision")
    diff.add_argument("--from", dest="from_rev", help="deepops revision to start every host from")
    diff.add_argument("--config-from", help="with --from: the config revision (default: config as it is)")
    diff.set_defaults(func=cmd_diff)
    show_p = sub.add_parser("show", help="every setting a host resolves")
    show_p.add_argument("host")
    show_p.add_argument("names", nargs="*", metavar="NAME", help="print only these settings' values")
    show_p.set_defaults(func=cmd_show)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
