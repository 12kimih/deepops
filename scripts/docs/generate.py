#!/usr/bin/env python3
"""Fill the generated blocks in the docs from the files they describe.

Docs that restate a version or a default go stale the next time the role
changes. Instead, a doc marks a block

    <!-- BEGIN GENERATED: versions -->
    <!-- END GENERATED: versions -->

and this script writes its body from the source (role defaults, the example
config). Hand edits inside a block are overwritten.

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

Usage: generate.py [--check]   (--check reports stale blocks and exits 1)
"""

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import cli  # noqa: E402
from repo_files import tracked  # noqa: E402

BLOCK = re.compile(
    r"(<!-- BEGIN GENERATED: (?P<name>[\w:./-]+) -->\n)(?P<body>.*?)(<!-- END GENERATED: (?P=name) -->)", re.S
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


def gen_versions(_arg):
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


def gen_role_variables(role):
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


def gen_tree(top):
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


def gen_role_tree(_arg):
    return gen_tree("roles")


SWITCH_NAME = re.compile(r"\b([a-z][a-z0-9]*_[a-z0-9_]+)\b")


def gen_components(_arg):
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


def gen_bot_subcommands(_arg):
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


def gen_exporters(_arg):
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


def gen_ports(_arg):
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


def gen_dashboards(_arg):
    sets, owners = defaults("prometheus_paths")["grafana_dashboard_sets"], dashboard_set_roles()
    rows = ["| Dashboard | UID | Shows | Installed by |", "|---|---|---|---|"]
    for name, files in sets.items():
        for file in files:
            board = json.loads((ROOT / "src" / "dashboards" / file).read_text(encoding="utf-8"))
            rows.append(
                f"| {cell(board['title'])} | `{board['uid']}` | {cell(board.get('description', ''))} | `{owners[name]}` |"
            )
    return "\n".join(rows) + "\n"


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
}


def render(match):
    name = match.group("name")
    kind, _, arg = name.partition(":")
    if kind not in GENERATORS:
        raise SystemExit(f"unknown generated block: {name}")
    return match.group(1) + GENERATORS[kind](arg) + match.group(4)


def malformed(text):
    """A marker the block pattern does not take (an END misspelt or missing, CRLF,
    trailing space) would leave its block ungenerated and --check passing."""
    return text.count("<!-- BEGIN GENERATED:") != len(BLOCK.findall(text))


def main(argv):
    parser = cli.parser(__doc__)
    parser.add_argument("--check", action="store_true", help="report stale files and exit 1, writing nothing")
    check = parser.parse_args(argv).check
    stale = []
    for path in sorted(tracked("*.md")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "<!-- BEGIN GENERATED:" not in text:
            continue
        if malformed(text):
            print(f"malformed generated block: {path.relative_to(ROOT)}", file=sys.stderr)
            return 1
        new = BLOCK.sub(render, text)
        if new != text:
            stale.append(path.relative_to(ROOT))
            if not check:
                path.write_text(new, encoding="utf-8")
    for path in stale:
        print(f"{'stale' if check else 'updated'}: {path}")
    return 1 if (check and stale) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
