#!/usr/bin/env python3
"""What each host should have of each component, as the checkouts are now: the version
its role would record there (component_record_version, resolved from the role defaults
and the site config) and the source digest a deploy of it would record
(component_source.py). Hosts report what they have from their component record
(os_updates_component_info, os_updates_component_source_info, roles/os_updates).

The expected version is the role's own expression, read from its component_record task,
never a list kept apart, resolved per host the way settings.py resolves a setting (the
owning default, then the config's group and host vars) and rendered with Ansible's
templating. A component whose version depends on what only the host knows (a fact) is
left unchecked, with the reason.

  component_expected.py [--config DIR] textfile [--out FILE]
      node_exporter textfile metrics, deepops_component_expected_{version,source}_info
      {for_host, component, ...}: roles/prometheus writes them into
      component_expected_dir at each prometheus.yml run, and scripts/deepops/sync.py
      after each move of the checkouts, so ComponentVersionNotPinned and
      ComponentNotDeployed (rules/os-updates.yml) expect what the checkouts hold now
  component_expected.py [--config DIR] changes --from REV [--config-from REV]
      hosts whose expected versions or sources changed between REV (and the config at
      REV) and the working trees, and the playbooks that bring them level

Nothing connects to a host and nothing is decrypted (a vaulted value is unknown).
"""

import os
import socket
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli  # noqa: E402
import component_source  # noqa: E402
import settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_MARKS = ("{{", "{%")


class Unchecked(str):
    """Why a component's expected version is not known (a str: the reason)."""


def expressions(tree=None):
    """{component: expression}: the version each role's component_record task records
    (action present), from TREE (the working tree by default). ValueError names a
    component two tasks record with different expressions."""
    tree = tree or settings.Tree(ROOT)
    found = {}
    for name, rel, args in component_source.records(tree):
        if args.get("component_record_action") != "present" or not args.get("component_record_version"):
            continue
        expr = str(args["component_record_version"])
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


class State:
    """Each host's expected versions and sources, for one deepops tree and config, the
    playbook of each component on each host (an expression resolved there) and the
    components the site holds back (deepops_deploy_holds, {name: reason})."""

    def __init__(self, versions, sources, playbooks=None, holds=None):
        self.versions, self.sources = versions, sources
        self.playbooks, self.holds = playbooks or {}, holds or {}


def host_playbooks(config, owner_map, playbooks, hosts=None):
    """({host: {component: playbook}}, holds): each playbook expression resolved for each
    host as its settings give it (left out where it cannot be), and the site's holds."""
    out, holds = {}, {}
    for host in sorted(hosts if hosts is not None else config.hosts):
        values = {name: value for name, (value, _) in config.resolve(host, owner_map).items()}
        row = {}
        for component, playbook in playbooks.items():
            got = render(playbook, values) if "{{" in str(playbook) else playbook
            if not isinstance(got, Unchecked):
                row[component] = str(got)
        out[host] = row
        held = values.get("deepops_deploy_holds")
        if isinstance(held, dict):
            holds.update({str(k): str(v) for k, v in held.items()})
    return out, holds


def state(config_dir, rev=None, config_rev=None):
    """State for the deepops tree at REV and the config at CONFIG_REV (None: as they
    are on disk)."""
    tree = settings.Tree(ROOT, rev=rev)
    config = settings.Config(settings.site_tree(config_dir, config_rev))
    owner_map = settings.owners(tree, private=True)
    import component_playbooks

    playbooks, holds = host_playbooks(config, owner_map, component_playbooks.playbooks(tree.root))
    return State(
        expected(config, owner_map, expressions(tree)),
        component_source.Sources(tree, config, owner_map).all(),
        playbooks,
        holds,
    )


def _labels(**labels):
    return ",".join(
        f'{k}="{str(v).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34)).replace(chr(10), chr(92) + "n")}"'
        for k, v in labels.items()
    )


def textfile(current, playbooks):
    """The node_exporter textfile: one series per host and checked component for the
    version, one per host and component for the source (with the playbook that deploys
    it), and the unchecked versions named in comments with their reasons. The same
    checkouts give the same text: a converged prometheus.yml rerun changes nothing."""
    unchecked = sorted(
        {(c, str(v)) for row in current.versions.values() for c, v in row.items() if isinstance(v, Unchecked)}
    )
    lines = [
        "# Written by scripts/deepops/component_expected.py (roles/prometheus at each",
        "# prometheus.yml run; scripts/deepops/sync.py after each move of the checkouts).",
    ]
    lines += [f"# Not checked: {c}: {why}" for c, why in unchecked]
    lines += [
        "# HELP deepops_component_expected_version_info The version a host should have of a component (value always 1).",
        "# TYPE deepops_component_expected_version_info gauge",
    ]
    for host, row in sorted(current.versions.items()):
        for component, version in sorted(row.items()):
            if not isinstance(version, Unchecked):
                lines.append(
                    f"deepops_component_expected_version_info{{{_labels(for_host=host, component=component, version=version)}}} 1"
                )
    lines += [
        "# HELP deepops_component_expected_source_info The source digest a deploy of a component to a host would record (value always 1).",
        "# TYPE deepops_component_expected_source_info gauge",
    ]
    for host, row in sorted(current.sources.items()):
        for component, source in sorted(row.items()):
            labels = {"for_host": host, "component": component, "source": source}
            playbook = current.playbooks.get(host, {}).get(component) or playbooks.get(component)
            if playbook and "{{" not in playbook:
                labels["playbook"] = playbook
            lines.append(f"deepops_component_expected_source_info{{{_labels(**labels)}}} 1")
    return "\n".join(lines) + "\n"


def expected_path(config_dir):
    """Where the expected component state goes on this machine (component_expected_dir
    and component_expected_file, resolved as for this host when the inventory names it,
    else as for its first host), or None when that needs what only a run knows."""
    tree = settings.Tree(ROOT)
    owner_map = settings.owners(tree, private=True)
    config = settings.Config(settings.site_tree(config_dir))
    here = socket.gethostname().split(".")[0]
    hosts = sorted(config.hosts)
    host = here if here in config.hosts else (hosts[0] if hosts else None)
    values = (
        {name: value for name, (value, _) in config.resolve(host, owner_map).items()}
        if host
        else {name: owner.value for name, owner in owner_map.items()}
    )
    out = render("{{ component_expected_dir }}/{{ component_expected_file }}", values)
    return None if isinstance(out, Unchecked) else Path(out)


def write(path, text):
    """TEXT into PATH atomically (node_exporter never reads half a file)."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


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


def moved(before, after):
    """{host: [component]} whose source digest differs (a host or component new on the
    AFTER side counts: nothing deployed it from these checkouts)."""
    out = {}
    for host, row in sorted(after.items()):
        old = before.get(host, {})
        names = [c for c, digest in sorted(row.items()) if old.get(c) != digest]
        if names:
            out[host] = names
    return out


def describe(found, sources, playbooks, per_host=None, holds=None):
    """Lines saying which hosts' expected versions and sources moved, and which
    playbooks bring them level. FOUND: changes(); SOURCES: moved(); PER_HOST: each
    host's own playbook of a component ({host: {component: playbook}}); HOLDS: the
    components the site holds back ({name: reason}), named apart and never to run."""
    lines, needed, wanted = [], set(), set()
    per_host, holds = per_host or {}, holds or {}
    if found:
        lines.append("component versions the checkouts now expect:")
        groups = {}
        for host, rows in found.items():
            groups.setdefault(tuple(rows), []).append(host)
        for rows, hosts in groups.items():
            lines.append(f"  {', '.join(hosts)}:")
            for component, b, a in rows:
                lines.append(f"    {component}: {b or '-'} -> {a or '-'}")
                needed.add(component)
                wanted |= {(h, component) for h in hosts}
    if sources:
        lines.append("components whose code or config changed (ComponentNotDeployed until deployed):")
        groups = {}
        for host, names in sources.items():
            groups.setdefault(tuple(names), []).append(host)
        for names, hosts in groups.items():
            lines.append(f"  {', '.join(hosts)}: {', '.join(names)}")
            needed.update(names)
            wanted |= {(h, c) for h in hosts for c in names}
    if not lines:
        return ["components: no host's expected version or source changed"]

    def playbook(host, component):
        found = per_host.get(host, {}).get(component) or playbooks.get(component)
        return found if found and "{{" not in found else None

    runs = sorted({playbook(h, c) for h, c in wanted if c not in holds and playbook(h, c)})
    if runs:
        lines.append("  run, to deploy them:")
        lines += [f"    uv run ansible-playbook -K {p}" for p in runs]
    held = sorted(c for c in needed if c in holds)
    if held:
        lines.append("  held back by the site (deepops_deploy_holds), not to run now:")
        lines += [f"    {c}: {holds[c]}" for c in held]
    rest = sorted(
        c for c in needed if c not in holds and not any(playbook(h, x) for h, x in wanted if x == c)
    )
    if rest:
        lines.append(f"  and the playbooks that apply: {', '.join(rest)}")
    return lines


def main(argv):
    parser = cli.parser(__doc__)
    parser.add_argument("--config", default=os.environ.get("DEEPOPS_CONFIG_DIR", str(ROOT / "config")))
    sub = parser.add_subparsers(dest="command", required=True)
    tf = sub.add_parser("textfile", help="the textfile metrics, on stdout")
    tf.add_argument("--out", help="write them to this file instead (atomically)")
    ch = sub.add_parser("changes", help="hosts whose expected versions or sources changed since a revision")
    ch.add_argument("--from", dest="from_rev", required=True, help="the deepops revision to compare with")
    ch.add_argument("--config-from", help="the config revision to compare with (default: as it is)")
    args = parser.parse_args(argv)
    import component_playbooks

    playbooks = component_playbooks.playbooks()
    if args.command == "textfile":
        text = textfile(state(args.config), playbooks)
        if args.out:
            write(args.out, text)
        else:
            sys.stdout.write(text)
        return 0
    before, after = state(args.config, args.from_rev, args.config_from), state(args.config)
    print("\n".join(report(before, after, playbooks)))
    return 0


def report(before, after, playbooks):
    """describe() of what moved from BEFORE to AFTER (two State)."""
    return describe(changes(before.versions, after.versions), moved(before.sources, after.sources), playbooks,
                    after.playbooks, after.holds)  # fmt: skip


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
