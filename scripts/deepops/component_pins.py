#!/usr/bin/env python3
"""The version each host should have of each component: what its role would record there
(component_record_version), resolved from the role defaults and the site config.

Each role records the version it installed in the host's component record
(roles/component_record), and every host reports what it has (os_updates_component_info,
roles/os_updates). The expected side is the role's own expression for that version, read
from its component_record task, never a list kept apart, resolved per host the way
settings.py resolves a setting (the owning default, then the config's group and host
vars) and rendered with Ansible's templating. A component whose version depends on what
only the host knows (a fact) is left unchecked, with the reason.

  component_pins.py [--config DIR] rules [--out FILE]
      the rules file roles/prometheus installs: deepops:component_pin:info{host,
      component, version} per host and component, so the ComponentVersionNotPinned
      alert (rules/os-updates.yml) names each host that has another version
  component_pins.py [--config DIR] changes --from REV [--config-from REV]
      hosts whose expected versions changed between REV (and the config at REV) and
      the working trees; scripts/deepops/sync.py prints this after it moves them

Nothing connects to a host and nothing is decrypted (a vaulted value is unknown).
"""

import json
import os
import sys
from pathlib import Path, PurePosixPath

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402
import settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RECORD = "component_record"
INCLUDES = ("ansible.builtin.include_role", "include_role", "ansible.builtin.import_role", "import_role")
TEMPLATE_MARKS = ("{{", "{%")


class Unchecked(str):
    """Why a component's expected version is not known (a str: the reason)."""


def _tasks(items):
    for task in items if isinstance(items, list) else []:
        if isinstance(task, dict):
            for key in ("block", "rescue", "always"):
                yield from _tasks(task.get(key))
            yield task


def expressions(tree=None):
    """{component: expression}: the version each role's component_record task records
    (action present), from TREE (the working tree by default). ValueError names a
    component two tasks record with different expressions."""
    tree = tree or settings.Tree(ROOT)
    found = {}
    for rel in tree.files("roles"):
        parts = PurePosixPath(rel).parts
        if (
            "tasks" not in parts
            or "molecule" in parts
            or "galaxy" in parts
            or not rel.endswith((".yml", ".yaml"))
        ):
            continue
        try:
            data = yaml.safe_load(tree.text(rel) or "")
        except yaml.YAMLError:
            continue
        for task in _tasks(data):
            include = next((task[k] for k in INCLUDES if isinstance(task.get(k), dict)), None)
            if not include or include.get("name") != RECORD:
                continue
            args = task.get("vars") or {}
            if args.get("component_record_action") != "present" or not args.get("component_record_version"):
                continue
            name, expr = str(args["component_record_name"]), str(args["component_record_version"])
            if found.setdefault(name, (expr, rel))[0] != expr:
                raise ValueError(f"{name}: {found[name][1]} and {rel} record different versions")
    return {name: expr for name, (expr, _) in sorted(found.items())}


def _trusted(value):
    """VALUE with every string marked as a template, as Ansible marks what it loads."""
    from ansible.template import trust_as_template

    if isinstance(value, str):
        return trust_as_template(value) if any(m in value for m in TEMPLATE_MARKS) else value
    if isinstance(value, dict):
        return {k: _trusted(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_trusted(v) for v in value]
    return value


def render(expr, values):
    """EXPR rendered with VALUES ({name: value}); Unchecked with the reason when it needs
    something VALUES do not hold (a fact, a variable set at run time, a vaulted value)."""
    from ansible.parsing.dataloader import DataLoader
    from ansible.template import Templar

    variables = {
        k: _trusted(v)
        for k, v in values.items()
        if not isinstance(v, settings.Vault) and v is not settings.MISSING and v is not settings.AMBIGUOUS
    }
    try:
        out = Templar(loader=DataLoader(), variables=variables).template(_trusted(expr))
    except Exception as e:  # undefined: a fact or a set_fact; Ansible words the name
        message = str(e).splitlines()[0]
        return Unchecked(f"needs what only the host or the run knows ({message})")
    return str(out)


def expected(config, owner_map, exprs, hosts=None):
    """{host: {component: version or Unchecked}} for HOSTS (default: the inventory's)."""
    out = {}
    for host in sorted(hosts if hosts is not None else config.hosts):
        values = {name: value for name, (value, _) in config.resolve(host, owner_map).items()}
        out[host] = {name: render(expr, values) for name, expr in exprs.items()}
    return out


def state(config_dir, rev=None, config_rev=None):
    """expected() for the deepops tree at REV and the config at CONFIG_REV (None: as
    they are on disk)."""
    tree = settings.Tree(ROOT, rev=rev)
    config = settings.Config(settings.site_tree(config_dir, config_rev))
    return expected(config, settings.owners(tree, private=True), expressions(tree))


def rules(pins):
    """The rules file's text: one series per host and checked component, and the
    unchecked ones named in its head with their reasons."""
    unchecked = sorted(
        {(c, str(v)) for row in pins.values() for c, v in row.items() if isinstance(v, Unchecked)}
    )
    lines = [
        "---",
        "# Rendered by scripts/deepops/component_pins.py at each prometheus.yml run, from the",
        "# role defaults and the site config: the version each host should have of each component.",
    ]
    lines += [f"# Not checked: {c}: {why}" for c, why in unchecked]
    lines += ["groups:", "  - name: component-pins", "    rules:"]
    for host, row in sorted(pins.items()):
        for component, version in sorted(row.items()):
            if isinstance(version, Unchecked):
                continue
            labels = ", ".join(
                f"{k}: {json.dumps(v)}"
                for k, v in (("host", host), ("component", component), ("version", version))
            )
            lines += [
                "      - record: deepops:component_pin:info",
                "        expr: vector(1)",
                f"        labels: {{{labels}}}",
            ]
    if not any(not isinstance(v, Unchecked) for row in pins.values() for v in row.values()):
        lines[-1] = "    rules: []"
    return "\n".join(lines) + "\n"


def changes(before, after):
    """{host: [(component, before, after)]} for each checked version that differs."""
    out = {}
    for host in sorted(set(before) | set(after)):
        b, a = before.get(host, {}), after.get(host, {})
        rows = [
            (c, b.get(c), a.get(c))
            for c in sorted(set(b) | set(a))
            if not isinstance(a.get(c), Unchecked) and b.get(c) != a.get(c)
        ]
        if rows:
            out[host] = rows
    return out


def describe(found, playbooks):
    """Lines saying which hosts' expected versions moved and which playbooks level them."""
    if not found:
        return ["component versions: no host's expected version changed"]
    lines = ["component versions the checkouts now expect:"]
    groups = {}
    for host, rows in found.items():
        groups.setdefault(tuple(rows), []).append(host)
    needed = set()
    for rows, hosts in groups.items():
        lines.append(f"  {', '.join(hosts)}:")
        for component, b, a in rows:
            lines.append(f"    {component}: {b or '-'} -> {a or '-'}")
            if component in playbooks:
                needed.add(playbooks[component])
    lines.append("  run, to install them (and so the alert expects them):")
    lines += [f"    uv run ansible-playbook -K {p}" for p in sorted(needed)]
    if playbooks.get("prometheus") not in needed:
        lines.append(
            f"    uv run ansible-playbook -K {playbooks.get('prometheus', 'playbooks/slurm-cluster/prometheus.yml')}"
        )
    return lines


def main(argv):
    parser = cli.parser(__doc__)
    parser.add_argument("--config", default=os.environ.get("DEEPOPS_CONFIG_DIR", str(ROOT / "config")))
    sub = parser.add_subparsers(dest="command", required=True)
    ru = sub.add_parser("rules", help="the rules file, on stdout")
    ru.add_argument("--out", help="write it to this file instead")
    ch = sub.add_parser("changes", help="hosts whose expected versions changed since a revision")
    ch.add_argument("--from", dest="from_rev", required=True, help="the deepops revision to compare with")
    ch.add_argument("--config-from", help="the config revision to compare with (default: as it is)")
    args = parser.parse_args(argv)
    if args.command == "rules":
        text = rules(state(args.config))
        if args.out:
            Path(args.out).write_text(text)
        else:
            sys.stdout.write(text)
        return 0
    import component_playbooks

    found = changes(state(args.config, args.from_rev, args.config_from), state(args.config))
    print("\n".join(describe(found, component_playbooks.playbooks())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
