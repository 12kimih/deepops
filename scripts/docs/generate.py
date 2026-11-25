#!/usr/bin/env python3
"""Fill the generated blocks in the docs from the files they describe.

Docs that restate a version or a default go stale the next time the role
changes. Instead, a doc marks a block

    <!-- BEGIN GENERATED: versions -->
    <!-- END GENERATED: versions -->

and this script writes its body from the source (role defaults, the example
config, the site config). Hand edits inside a block are overwritten. A single value
may sit inline, both markers on one line, so it reads inside a sentence, a list item
or a table cell:

    runs <!-- BEGIN GENERATED: setting:submit_holds_interval:every -->every 5 minutes<!-- END GENERATED: setting:submit_holds_interval:every -->

The pages are deepops' tracked *.md and, when the site config (DEEPOPS_CONFIG_DIR,
default config/) has them, its README.md and admin-docs/*.md. Under a commit hook the
site is read as settings.site_rev() says (what is being committed, or its HEAD).

Generators:
  versions              every pinned version, image and chart in role defaults, and
                        each package an example's requirements file pins
  role-variables:ROLE   the variables of roles/ROLE/defaults/main.yml, with the
                        comment above each one as its description
  tree:DIR              what is directly under DIR: its directories holding tracked
                        files, its submodules and its tracked files, as a tree
  role-tree             tree:roles (Galaxy's roles/galaxy left out)
  components            every component of the registry (roles/deploy_defaults/vars/main.yml):
                        its role, the switches and groups that place it, what it needs
                        and whether its removal is explicit
  bot-subcommands       the cluster bot's subcommands (cluster_bot.commands
                        SUBCOMMANDS), with the default command
  exporters             what Prometheus scrapes (prometheus_exporters, roles/prometheus_paths):
                        each exporter's groups (from the registry) and port setting
  ports                 every port a role listens on or sends to (a *_port default): its
                        default, protocol, setting and role, by port
  dashboards            every dashboard deepops ships (grafana_dashboard_sets): its title,
                        UID and description from its JSON, and the role that installs it
Only in the site's pages (deepops' are public and site-agnostic):
  setting:PATH[@HOST|@GROUP][:FORMAT]
                        a setting as the site resolves it (scripts/deepops/settings.py:
                        role default, then the config; templates rendered). PATH is the
                        name, then .key or .index into its value. Every inventory host,
                        or every host of GROUP, must agree, else the block fails
  fact:PATH[:FORMAT]    a value of scripts/docs/site_facts.py facts(), by dotted PATH
  group:GROUP           the inventory hosts of GROUP
  partitions            the partitions of the site's slurm.conf as a table, each with
                        its nodes and GPUs, and the GPUs of each type in all
FORMAT: none (a list as comma-joined `code`, else the value), code, span (a duration:
seconds, or a systemd or fail2ban span as 3d; a negative one is forever), every (a
systemd OnCalendar or span as how often). Words are Korean on a page that has Hangul.

Usage: generate.py [--check] [--site]   (--check reports stale blocks and exits 1;
--site: the site's pages only)
"""

import json
import functools
import importlib.util
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import cli  # noqa: E402
import settings  # noqa: E402
from repo_files import tracked  # noqa: E402

NAME = r"(?P<name>[\w:./@-]+)"
BLOCK = re.compile(
    rf"(<!-- BEGIN GENERATED: {NAME} -->\n)(?P<body>.*?)(<!-- END GENERATED: (?P=name) -->)", re.S
)
INLINE = re.compile(
    rf"(<!-- BEGIN GENERATED: {NAME} -->)(?P<body>[^\n]*?)(<!-- END GENERATED: (?P=name) -->)"
)
# The site's pages, under its config directory.
SITE_PAGES = ("README.md", "admin-docs/*.md")
# What the site generators read of a revision of the site (site_facts.py, settings.py),
# as paths: git ls-tree takes no glob.
SITE_FILES = (
    "inventory",
    "group_vars",
    "host_vars",
    "schedule.yml",
    "files/slurm",
    "README.md",
    "admin-docs",
)
PIN = re.compile(r"(version|_container|_image|_tag|_branch)$|_version_|_chart_version$")


def literal(value):
    return isinstance(value, (str, int, float)) and not isinstance(value, bool) and "{{" not in str(value)


def defaults(role):
    path = ROOT / "roles" / role / "defaults" / "main.yml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def example_values():
    found = {}
    for path in sorted(tracked("config.example/group_vars/*.yml")):
        for name, value in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).items():
            if literal(value):
                found[name] = str(value)
    return found


def roles():
    return sorted({p.parts[-3] for p in tracked("roles/*/defaults/main.yml")})


def cell(text):
    return str(text).replace("|", "\\|")


def gen_versions(_arg, _page=None):
    example = example_values()
    rows = ["| Role | Variable | Default | config.example |", "|---|---|---|---|"]
    for role in roles():
        for name, value in defaults(role).items():
            # A pin looks like a version (1.2.3, v1.2, a commit) or an image reference.
            if (
                PIN.search(name)
                and literal(value)
                and re.match(
                    r"^(v?\d|[0-9a-f]{12,}$|[\w./-]+:[\w.-]+(@sha256:[0-9a-f]{64})?$|[\w.-]+\.tar)",
                    str(value),
                )
            ):
                # Images are pinned by digest; a 12-character prefix identifies it in the table.
                shown = re.sub(r"(@sha256:[0-9a-f]{12})[0-9a-f]{52}$", r"\1", str(value))
                rows.append(
                    f"| {role} | `{name}` | `{cell(shown)}` | {('`' + cell(example[name]) + '`') if name in example and example[name] != str(value) else ''} |"
                )
    # The Python packages an example pins (its requirements file), by the example.
    for path in sorted(tracked("examples/*/requirements*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Za-z0-9_.-]+)==([^\s#;]+)", line)
            if m:
                rows.append(
                    f"| {path.parent.relative_to(ROOT).as_posix()} | `{m.group(1)}` | `{m.group(2)}` | |"
                )
    return "\n".join(rows) + "\n"


def comments_above(path):
    """Map each top-level variable to the comment block right above it."""
    notes, pending = {}, []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            pending = []
            continue
        if line.startswith("#"):
            # `# upstream:` says where a pin's releases come from (scripts/deepops/upstream.py),
            # not what the setting means.
            if not re.match(r"#\s*upstream:", line):
                pending.append(line.lstrip("#").strip())
            continue
        m = re.match(r"^([a-z_][a-z0-9_]*):", line)
        if m:
            notes[m.group(1)] = " ".join(p for p in pending if p)
        pending = []
    return notes


def gen_role_variables(role, _page=None):
    path = ROOT / "roles" / role / "defaults" / "main.yml"
    notes = comments_above(path)
    rows = ["| Variable | Default | Description |", "|---|---|---|"]
    for name, value in defaults(role).items():
        shown = (
            "`"
            + cell(
                yaml.safe_dump(value, default_flow_style=True, width=1000).strip().removesuffix("...").strip()
            )
            + "`"
        )
        rows.append(f"| `{name}` | {shown} | {cell(notes.get(name, ''))} |")
    return "\n".join(rows) + "\n"


def gen_tree(top, _page=None):
    """What is directly under TOP (a path in the repository): directories holding a
    tracked file, submodules and tracked files. Tracked paths only, like every other
    check: what another checkout has not committed yet must not make a page stale for
    everyone."""
    depth = len(Path(top).parts)
    names = set()
    for path in tracked(top):
        parts = path.relative_to(ROOT).parts
        if len(parts) > depth:
            names.add(parts[depth] + ("/" if len(parts) > depth + 1 or path.is_dir() else ""))
    if top == "roles":
        names.discard("galaxy/")
    names = sorted(names, key=lambda n: (not n.endswith("/"), n))
    branches = ["├──"] * (len(names) - 1) + ["└──"]
    tree = "\n".join(f"│   {b} {n.rstrip('/')}" for b, n in zip(branches, names))
    return f"```text\n├── {top}\n{tree}\n```\n"


def gen_role_tree(_arg, _page=None):
    return gen_tree("roles")


SWITCH_NAME = re.compile(r"\b([a-z][a-z0-9]*_[a-z0-9_]+)\b")


def gen_components(_arg, _page=None):
    path = ROOT / "roles" / "deploy_defaults" / "vars" / "main.yml"
    registry = yaml.safe_load(path.read_text(encoding="utf-8"))["deepops_component_registry"]
    rows = ["| Component | Role | Switches | Hosts | Needs | Removal |", "|---|---|---|---|---|---|"]
    for name, entry in registry.items():
        rules = entry.get("rules") or []
        switches = sorted({w for r in rules for w in SWITCH_NAME.findall(str(r.get("switch", "")))})
        hosts = [
            ("the first " if r.get("first") else "") + f"`{r['group']}`" for r in rules if r.get("group")
        ]
        rows.append("| " + " | ".join([
            f"`{name}`",
            f"`{entry.get('role', name)}`",
            ", ".join(f"`{w}`" for w in switches) or "-",
            ", ".join(dict.fromkeys(hosts)) or "-",
            ", ".join(f"`{n}`" for n in entry.get("needs") or []) or "-",
            entry.get("removal", "with its switch"),
        ]) + " |")  # fmt: skip
    return "\n".join(rows) + "\n"


def gen_bot_subcommands(_arg, _page=None):
    sys.path.insert(0, str(ROOT / "roles" / "cluster_bot" / "files"))
    from cluster_bot import commands

    command = defaults("cluster_bot")["cluster_bot_command"]
    return "\n".join(commands.doc_lines(command)) + "\n"


def registry():
    path = ROOT / "roles" / "deploy_defaults" / "vars" / "main.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["deepops_component_registry"]


def setting(template):
    """The variable a "{{ name }}" value names."""
    return re.fullmatch(r"\{\{\s*(\w+)\s*\}\}", str(template)).group(1)


def gen_exporters(_arg, _page=None):
    paths, components = defaults("prometheus_paths"), registry()
    rows = ["| Exporter | Runs on | Port |", "|---|---|---|"]
    for exporter in paths["prometheus_exporters"]:
        groups = [
            f"`{r['group']}`" for r in components[exporter["component"]].get("rules") or [] if r.get("group")
        ]
        port = setting(exporter["port"])
        rows.append(
            f"| `{exporter['module']}` | {', '.join(dict.fromkeys(groups))} | `{port}` ({paths[port]}) |"
        )
    slurmctld = defaults("slurm_paths")["slurm_slurmctld_port"]
    rows.append(f"| slurmctld (Slurm itself) | the controller | `slurm_slurmctld_port` ({slurmctld}) |")
    return "\n".join(rows) + "\n"


def gen_ports(_arg, _page=None):
    rows = []
    for role in roles():
        for name, value in defaults(role).items():
            if name.endswith("_port") and str(value).isdigit():
                protocol = "udp" if "_udp_" in name else "tcp"
                rows.append((int(value), protocol, name, role))
    lines = ["| Port | Setting | Role |", "|---|---|---|"]
    lines += [f"| {port}/{protocol} | `{name}` | `{role}` |" for port, protocol, name, role in sorted(rows)]
    return "\n".join(lines) + "\n"


def dashboard_set_roles():
    """{dashboard set: the role that installs it}, from each role's own setting of it."""
    found = {}
    for path in tracked("roles/*/vars/main.yml") + tracked("roles/*/defaults/main.yml"):
        for name, value in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).items():
            if re.search(r"dashboard_set(_own)?$", name) and isinstance(value, str) and "{{" not in value:
                found[value] = path.parts[-3]
    return found


def gen_dashboards(_arg, _page=None):
    sets, owners = defaults("prometheus_paths")["grafana_dashboard_sets"], dashboard_set_roles()
    rows = ["| Dashboard | UID | Shows | Installed by |", "|---|---|---|---|"]
    for name, files in sets.items():
        for file in files:
            board = json.loads((ROOT / "src" / "dashboards" / file).read_text(encoding="utf-8"))
            rows.append(
                f"| {cell(board['title'])} | `{board['uid']}` | {cell(board.get('description', ''))} | `{owners[name]}` |"
            )
    return "\n".join(rows) + "\n"


# The site's values -------------------------------------------------------------------


def _site_facts():
    spec = importlib.util.spec_from_file_location("site_facts", ROOT / "scripts" / "docs" / "site_facts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Site:
    """The site config a page is generated from: its settings per host and its facts."""

    def __init__(self, root):
        self.root = Path(root)

    @functools.cached_property
    def facts_module(self):
        return _site_facts()

    @functools.cached_property
    def facts(self):
        return self.facts_module.facts(str(self.root))

    @functools.cached_property
    def config(self):
        return settings.Config(settings.site_tree(self.root))

    @functools.cached_property
    def owners(self):
        return settings.owners()

    def hosts(self, target):
        if not target:
            return sorted(self.config.hosts)
        if target in self.config.hosts:
            return [target]
        if self.config.group_hosts.get(target):
            return sorted(self.config.group_hosts[target])
        raise SystemExit(f"setting:...@{target}: the inventory has no such host or group with hosts")

    def setting(self, path, target):
        """PATH's value on every host of TARGET (all hosts without one); they must agree."""
        name, *keys = path.split(".")
        found = {}
        for host in self.hosts(target):
            raw = {n: v for n, (v, _) in self.config.resolve(host, self.owners).items()}
            if name not in raw or raw[name] is settings.MISSING:
                raise SystemExit(f"setting:{name}: no role default or config sets it")
            value = self.facts_module.resolved(name, raw)
            if value is None:
                raise SystemExit(
                    f"setting:{name}: {host} resolves it only with what a host knows (or it is vaulted)"
                )
            found[host] = dig(value, keys, f"setting:{path}")
        values = list(found.values())
        if any(v != values[0] for v in values):
            hosts = ", ".join(sorted(found))
            raise SystemExit(
                f"setting:{path}: the hosts differ ({hosts}); name one: setting:{path}@HOST or @GROUP"
            )
        return values[0]


def dig(value, keys, label):
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            raise SystemExit(f"{label}: no {key!r} in {value!r}")
    return value


# Formats -----------------------------------------------------------------------------

# Seconds per unit of a systemd (systemd.time(7)) or fail2ban time span; m is minutes in both.
SPAN_UNITS = {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600, "d": 86400, "w": 604800}
WORDS = {
    "ko": {"day": "{}일", "hour": "{}시간", "minute": "{}분", "second": "{}초", "forever": "영구",
           "every": "{}마다", "hourly": "매시간", "daily": "매일 {}"},
    "en": {"day": "{} days", "hour": "{} hours", "minute": "{} minutes", "second": "{} seconds",
           "forever": "forever", "every": "every {}", "hourly": "every hour", "daily": "daily at {}"},
}  # fmt: skip
STEPS = (("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1))


def seconds(value):
    """A duration in seconds: a number is seconds; a span is "5min", "3d", "1h 30min"."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    parts = re.findall(r"(-?\d+)\s*([a-z]*)", str(value).strip())
    if not parts or re.sub(r"(-?\d+)\s*([a-z]*)", "", str(value)).strip():
        raise SystemExit(f"span: {value!r} is not a duration")
    total = 0
    for number, unit in parts:
        if (unit or "s") not in SPAN_UNITS:
            raise SystemExit(f"span: {value!r}: unknown unit {unit!r}")
        total += int(number) * SPAN_UNITS[unit or "s"]
    return total


def span(value, lang):
    total, words = seconds(value), WORDS[lang]
    if total < 0:
        return words["forever"]
    out = []
    for unit, size in STEPS:
        if total >= size:
            n, total = divmod(total, size)
            text = words[unit].format(n)
            out.append(text[:-1] if lang == "en" and n == 1 else text)
    return " ".join(out) or words["second"].format(0)


def every(value, lang):
    """How often a systemd OnCalendar (the forms used here) or a time span fires."""
    words, text = WORDS[lang], str(value).strip()
    m = re.fullmatch(r"(?:\*-\*-\* )?\*:0?0/(\d+)(?::00)?", text)
    if m:
        text = f"{m.group(1)}min"
    elif text in ("hourly", "*:00", "*:0", "*-*-* *:00:00"):
        return words["hourly"]
    m = re.fullmatch(r"(?:\*-\*-\* )?(\d\d:\d\d)(?::00)?", text)
    if m or text == "daily":
        return words["daily"].format(m.group(1) if m else "00:00")
    one = span(text, lang)
    if lang == "en" and re.fullmatch(r"1 \w+", one):  # every minute, not every 1 minute
        one = one[2:]
    return words["every"].format(one)


def code(value):
    return f"`{value}`"


def show(value, form, lang, label):
    if form == "span":
        return span(value, lang)
    if form == "every":
        return every(value, lang)
    if form not in ("", "code"):
        raise SystemExit(f"{label}: unknown format {form!r} (code, span, every)")
    if isinstance(value, list):
        return ", ".join(code(v) for v in value)
    if isinstance(value, dict) or isinstance(value, bool) or value is None:
        raise SystemExit(f"{label}: {value!r} is no single value; name a key (.key) or a format")
    return code(value) if form == "code" else str(value)


# Site generators ---------------------------------------------------------------------


@dataclass
class Page:
    lang: str  # "ko" on a page with Hangul, else "en"
    site: Site = None


def _site(page, name):
    if page is None or page.site is None:
        raise SystemExit(f"{name}: a site generator, only in the site config's pages")
    return page.site


def gen_setting(arg, page=None):
    site = _site(page, "setting")
    where, _, form = arg.partition(":")
    path, _, target = where.partition("@")
    return show(site.setting(path, target), form, page.lang, f"setting:{arg}") + "\n"


def gen_fact(arg, page=None):
    site = _site(page, "fact")
    path, _, form = arg.partition(":")
    value = dig(site.facts, path.split("."), f"fact:{path}")
    return show(value, form, page.lang, f"fact:{arg}") + "\n"


def gen_group(group, page=None):
    site = _site(page, "group")
    if not site.config.group_hosts.get(group):
        raise SystemExit(f"group:{group}: the inventory has no such group with hosts")
    return ", ".join(code(h) for h in sorted(site.config.group_hosts[group])) + "\n"


def gpus(gres):
    """{type: count} of a node's Gres (gpu:TYPE:N, comma-separated)."""
    out = {}
    for item in filter(None, gres.split(",")):
        parts = item.split("(")[0].split(":")
        if parts[0] == "gpu" and len(parts) == 3 and parts[2].isdigit():
            out[parts[1]] = out.get(parts[1], 0) + int(parts[2])
    return out


def gen_partitions(_arg, page=None):
    site = _site(page, "partitions")
    cluster = site.facts["cluster"]  # site_facts puts cluster_tools on the path
    from cluster_tools.slurm import hostnames

    ko = page.lang == "ko"
    nodes = {n["name"]: gpus(n["gres"]) for n in cluster["nodes"]}
    rows = [
        "| 파티션 | 노드 (노드당 GPU) | GPU | 최대 시간 |"
        if ko
        else "| Partition | Nodes (GPUs each) | GPUs | MaxTime |",
        "|---|---|---|---|",
    ]
    for part in cluster["partitions"]:
        names = hostnames(part["nodes"])
        counts = [sum(nodes.get(n, {}).values()) for n in names]
        if len(set(counts)) == 1 and len(names) > 1:
            shown = ", ".join(code(n) for n in names) + (
                f" (각 {counts[0]})" if ko else f" ({counts[0]} each)"
            )
        else:
            shown = ", ".join(f"{code(n)} ({c})" for n, c in zip(names, counts))
        total = {}
        for n in names:
            for kind, c in nodes.get(n, {}).items():
                total[kind] = total.get(kind, 0) + c
        types = ", ".join(f"{code(k)} {c}" for k, c in sorted(total.items())) or "-"
        rows.append(f"| {code(part['name'])} | {shown} | {types} | {part['max_time'] or '-'} |")
    every_gpu = {}
    for kinds in nodes.values():
        for kind, c in kinds.items():
            every_gpu[kind] = every_gpu.get(kind, 0) + c
    summary = ", ".join(f"{code(k)} {c}" for k, c in sorted(every_gpu.items()))
    return "\n".join(rows) + f"\n\n{'GPU 합계' if ko else 'GPUs in all'}: {summary}\n"


GENERATORS = {
    "components": gen_components,
    "exporters": gen_exporters,
    "dashboards": gen_dashboards,
    "ports": gen_ports,
    "versions": gen_versions,
    "role-variables": gen_role_variables,
    "role-tree": gen_role_tree,
    "tree": gen_tree,
    "bot-subcommands": gen_bot_subcommands,
    "setting": gen_setting,
    "fact": gen_fact,
    "group": gen_group,
    "partitions": gen_partitions,
}


def body(name, page):
    kind, _, arg = name.partition(":")
    if kind not in GENERATORS:
        raise SystemExit(f"unknown generated block: {name}")
    return GENERATORS[kind](arg, page)


def fill(text, page):
    """TEXT with every block and inline block generated."""

    def block(m):
        return m.group(1) + body(m.group("name"), page) + m.group(4)

    def inline(m):
        value = body(m.group("name"), page).strip()
        if "\n" in value:
            raise SystemExit(f"{m.group('name')}: more than one line; use the block form")
        return m.group(1) + value + m.group(4)

    return INLINE.sub(inline, BLOCK.sub(block, text))


def malformed(text):
    """A marker the block patterns do not take (an END misspelt or missing, CRLF,
    trailing space) would leave its block ungenerated and --check passing."""
    return text.count("<!-- BEGIN GENERATED:") != len(BLOCK.findall(text)) + len(INLINE.findall(text))


def language(text):
    return "ko" if re.search("[\uac00-\ud7a3]", text) else "en"


def site_pages(root):
    return sorted(p for pattern in SITE_PAGES for p in Path(root).glob(pattern) if p.is_file())


def export_site(config, rev, into):
    """The SITE_FILES of the site config CONFIG at REV, written under INTO."""
    git = functools.partial(
        subprocess.run, cwd=config, capture_output=True, env=settings.CLEAN_ENV, check=True
    )
    listing = git(["git", "ls-tree", "-r", "-z", "--name-only", rev, "--", *SITE_FILES]).stdout
    names = [n.decode() for n in listing.split(b"\0") if n]
    if names:
        archive = git(["git", "archive", "--format=tar", rev, "--", *names]).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(into, filter="data")


def generate(pages, check, site=None, shown=lambda p: p):
    """Fill PAGES; (stale pages, error or None)."""
    stale = []
    for path in pages:
        text = path.read_text(encoding="utf-8")
        if "<!-- BEGIN GENERATED:" not in text:
            continue
        if malformed(text):
            return stale, f"malformed generated block: {shown(path)}"
        new = fill(text, Page(language(text), site))
        if new != text:
            stale.append(shown(path))
            if not check:
                path.write_text(new, encoding="utf-8")
    return stale, None


def main(argv):
    parser = cli.parser(__doc__)
    parser.add_argument("--check", action="store_true", help="report stale files and exit 1, writing nothing")
    parser.add_argument("--site", action="store_true", help="the site config's pages only")
    args = parser.parse_args(argv)
    stale = []
    if not args.site:
        pages = [p for p in sorted(tracked("*.md")) if p.is_file()]
        stale, error = generate(pages, args.check, shown=lambda p: p.relative_to(ROOT))
        if error:
            print(error, file=sys.stderr)
            return 1
    config = Path(os.environ.get("DEEPOPS_CONFIG_DIR", ROOT / "config"))
    rev = settings.site_rev(config) if args.check and config.is_dir() else None
    with tempfile.TemporaryDirectory() as tmp:
        root = config
        if rev:
            export_site(config, rev, tmp)
            root = Path(tmp)
        pages = site_pages(root) if root.is_dir() else []
        if pages:
            found, error = generate(
                pages, args.check, Site(root), shown=lambda p: Path("config") / p.relative_to(root)
            )
            if error:
                print(error, file=sys.stderr)
                return 1
            stale += found
    for path in stale:
        print(f"{'stale' if args.check else 'updated'}: {path}")
    if args.check and stale:
        print(
            "run: uv run python scripts/docs/generate.py (in deepops), and commit the pages", file=sys.stderr
        )
    return 1 if (args.check and stale) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
