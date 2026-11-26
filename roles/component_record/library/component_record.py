#!/usr/bin/python
"""Keep the host's record of the components deepops installed and what each needs."""

DOCUMENTATION = r"""
module: component_record
short_description: Record a component's state and the items it needs, with owner sets
description:
  - Keeps one JSON file (an Ansible local fact) per host. C(components) maps a component
    to its state (C(installing) or C(present)) and version. C(items) maps every item a
    component needs (C(package:NAME), C(unit:NAME), C(container:NAME), C(file:PATH),
    C(dir:PATH), C(mount:MOUNTPOINT) for an fstab entry) to the set of components that
    need it.
  - An item that no component needs any longer is an orphan. Orphans leave the record
    and are returned, so the caller removes exactly them and nothing another component
    still needs (docs/deepops/component-state.md).
  - Supports check mode; nothing is written then.
options:
  path:
    description: The record file.
    type: path
    default: /etc/ansible/facts.d/deepops_components.fact
  name:
    description: The component.
    type: str
    required: true
  action:
    description:
      - C(begin) records the component as installing and adds I(items) to what it needs,
        before anything is installed, so a failed run leaves items absent can find.
      - C(present) records it as present at I(version); I(items) is the whole set it now
        needs, so items only an earlier version needed become orphans.
      - C(absent) drops the component; I(items) adopts what it installed before the
        record existed (only items no other component needs become orphans), and only
        what shows deepops made it, so a site's own file or service of the same name is
        never removed: a file whose name or text says deepops or names I(role)
        (module_utils/deepops_marks.py: made_by; "Ansible managed" is any Ansible's);
        a unit whose unit file does; a container named deepops, or named as such a unit;
        a directory named deepops or holding an adopted file; a package named in
        I(exclusive_packages) (another role may install it unrecorded); an fstab entry as
        named (only explicit removals have them); a file whose whole content an earlier
        deepops version wrote unmarked, by I(legacy).
    type: str
    choices: [begin, present, absent]
    required: true
  items:
    description: Items as KIND:ID.
    type: list
    elements: str
    default: []
  exclusive_packages:
    description: Packages only this component installs, which C(absent) may adopt.
    type: list
    elements: str
    default: []
  legacy:
    description:
      - For a C(file:) item an earlier deepops version wrote without a mark, regular
        expressions of which one must match the file's WHOLE content (fullmatch) for
        C(absent) to adopt it. Shaped on deepops' own text, so a site's file of the same
        name, or one edited by hand, never matches. The component declares them beside
        its items (C(<role>_legacy) in its vars/main.yml).
    type: dict
    default: {}
  role:
    description: The role that writes the component's files ("# Managed by roles/ROLE").
    type: str
    default: ""
  write:
    description:
      - False computes the record and the orphans without writing it. The role calls it
        so first, removes the orphans, then writes: a removal that fails midway leaves
        the record as it was, so the next run finds the same orphans again.
    type: bool
    default: true
  version:
    description: The component's version, for C(begin) and C(present).
    type: str
    default: ""
  source:
    description:
      - The digest of what decides the component on this host
        (scripts/deepops/component_source.py), recorded with C(present). Empty keeps the
        one recorded before: a run that may have skipped part of the role (--tags,
        --check, failed hosts) must not mark the component current.
    type: str
    default: ""
  record_source:
    description: The digest of component_record itself, kept beside the components
      (C(record_source)); empty keeps the one recorded before.
    type: str
    default: ""
"""

RETURN = r"""
orphans:
  description: Items no component needs any longer, to remove, grouped by kind
    (package, unit, container, file, dir, mount).
  type: dict
  returned: always
before:
  description: The component's entry before this call (empty when it had none).
  type: dict
  returned: always
written:
  description: Whether the record file was written (a new source digest alone too,
    which is not a change).
  type: bool
  returned: always
"""

import json
import os
import re
import tempfile

from ansible.module_utils import deepops_command, deepops_diff
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.deepops_marks import made_by, named_deepops

KINDS = ("package", "unit", "container", "file", "dir", "mount")


def dump(record):
    """The record file's text: what is written and what a diff compares."""
    return json.dumps(record, indent=1, sort_keys=True) + "\n"


def load(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    data.setdefault("components", {})
    data.setdefault("items", {})
    return data


def legacy_match(text, patterns):
    """Whether TEXT, a file's whole content, is what an earlier deepops version wrote:
    one of PATTERNS matches all of it (a pure function, for the tests)."""
    return any(re.fullmatch(pattern, text) for pattern in patterns or [])


def adoptable(items, owned, exclusive=(), evidence=lambda kind, ident: False):
    """The ITEMS absent may take on without a record of them (OWNED: those it has one
    for), by what shows deepops made each: EVIDENCE(kind, ident) for a file, a unit or a
    directory's name (a pure function, for the tests)."""
    split = [(item, *item.partition(":")[::2]) for item in items]
    taken = {i for i, kind, ident in split if i in owned}
    for item, kind, ident in split:
        if kind == "package" and ident in exclusive:
            taken.add(item)
        elif kind in ("file", "unit") and evidence(kind, ident):
            taken.add(item)
        elif kind == "mount":
            taken.add(item)
    files = [i.partition(":")[2] for i in taken if i.startswith("file:")]
    units = {i.partition(":")[2] for i in taken if i.startswith("unit:")}
    for item, kind, ident in split:
        if kind == "container" and ("deepops" in ident.lower() or ident in units):
            taken.add(item)
        elif kind == "dir" and (
            evidence("dir", ident) or any(f.startswith(ident.rstrip("/") + "/") for f in files)
        ):
            taken.add(item)
    return taken


def _with_source(entry, source):
    return {**entry, "source": source} if source else entry


def bookkeeping(record):
    """RECORD without its source digests: what a change to the host is judged by (a new
    digest alone is bookkeeping, as roles/deploy_record's is, never a change)."""
    return {
        "components": {
            n: {k: v for k, v in e.items() if k != "source"} for n, e in record["components"].items()
        },
        "items": record["items"],
    }


def apply(
    data,
    name,
    action,
    items,
    version,
    exclusive=(),
    evidence=lambda kind, ident: False,
    source="",
    record_source="",
):
    """The new record and the orphans (a pure function, for the tests). SOURCE and
    RECORD_SOURCE, when given, replace the recorded digests; otherwise they are kept."""
    components = dict(data["components"])
    owners = {item: set(names) for item, names in data["items"].items()}
    before = components.get(name, {})
    kept = before.get("source", "")
    if action == "begin":
        # A converged rerun (present at this version, every item already its own) leaves
        # the record as it is; new items or another version go through installing. A
        # begin given no version (most roles name it only once installed, at present)
        # compares the state and the items alone: a rerun must not rewrite the record.
        converged = (
            before.get("state") == "present"
            and version in ("", before.get("version"))
            and all(name in owners.get(item, ()) for item in items)
        )
        if not converged:
            components[name] = _with_source({"state": "installing", "version": version}, kept)
        for item in items:
            owners.setdefault(item, set()).add(name)
    elif action == "present":
        components[name] = _with_source({"state": "present", "version": version}, source or kept)
        for item, names in owners.items():
            if item not in items:
                names.discard(name)
        for item in items:
            owners.setdefault(item, set()).add(name)
    else:
        components.pop(name, None)
        owned = {item for item in items if name in owners.get(item, ())}
        for item in adoptable(items, owned, exclusive, evidence):
            owners.setdefault(item, set()).add(name)
        for names in owners.values():
            names.discard(name)
    orphans = sorted(item for item, names in owners.items() if not names)
    record = {
        "components": components,
        "items": {item: sorted(names) for item, names in sorted(owners.items()) if names},
    }
    if record_source or data.get("record_source"):
        record["record_source"] = record_source or data["record_source"]
    grouped = {kind: [] for kind in KINDS}
    for item in orphans:
        kind, _, ident = item.partition(":")
        grouped[kind].append(ident)
    # What starts a service stops first (a socket, a timer or a path unit), or it could
    # start the service again between the two stops.
    grouped["unit"].sort(key=lambda unit: (not unit.endswith((".socket", ".timer", ".path")), unit))
    return record, grouped, before


def main():
    module = AnsibleModule(
        argument_spec={
            "path": {"type": "path", "default": "/etc/ansible/facts.d/deepops_components.fact"},
            "name": {"type": "str", "required": True},
            "action": {"type": "str", "required": True, "choices": ["begin", "present", "absent"]},
            "items": {"type": "list", "elements": "str", "default": []},
            "version": {"type": "str", "default": ""},
            "exclusive_packages": {"type": "list", "elements": "str", "default": []},
            "legacy": {"type": "dict", "default": {}},
            "write": {"type": "bool", "default": True},
            "role": {"type": "str", "default": ""},
            "source": {"type": "str", "default": ""},
            "record_source": {"type": "str", "default": ""},
        },
        supports_check_mode=True,
    )
    p = module.params
    bad = [i for i in p["items"] if i.partition(":")[0] not in KINDS or not i.partition(":")[2]]
    if bad:
        module.fail_json(msg=f"items must be KIND:ID with KIND one of {', '.join(KINDS)}: {bad}")
    files = {i.partition(":")[2] for i in p["items"] if i.startswith("file:")}
    stray = sorted(set(p["legacy"]) - files)
    if stray:
        module.fail_json(msg=f"legacy names files that are not file items: {stray}")
    for path, patterns in p["legacy"].items():
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as e:
                module.fail_json(msg=f"legacy pattern for {path} does not compile: {e}")
    data = load(p["path"])

    def legacy(path):
        try:
            with open(path, errors="replace") as f:
                return legacy_match(f.read(1 << 20), p["legacy"].get(path))
        except OSError:
            return False

    def evidence(kind, ident):
        # Only what is on the host is evidence: a path named deepops that a host never
        # had (grafana's dashboards on a node without Grafana) is nothing to remove.
        if kind in ("file", "dir") and not os.path.lexists(ident):
            return False
        if kind == "file":
            return made_by(ident, p["role"]) or (ident in p["legacy"] and legacy(ident))
        if kind == "dir":
            return named_deepops(ident)
        rc, out, _ = deepops_command.run(module, ["systemctl", "show", "-P", "FragmentPath", ident])
        return rc == 0 and bool(out.strip()) and made_by(out.strip(), p["role"])

    record, orphans, before = apply(
        data,
        p["name"],
        p["action"],
        p["items"],
        p["version"],
        set(p["exclusive_packages"]),
        evidence,
        p["source"],
        p["record_source"],
    )
    changed = bookkeeping(record) != bookkeeping(data)
    # Written, a source digest alone too: what exports the record runs again (its readers).
    written = record != data and p["write"] and not module.check_mode
    if written:
        os.makedirs(os.path.dirname(p["path"]), mode=0o755, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p["path"]))
        with os.fdopen(fd, "w") as f:
            f.write(dump(record))
        os.chmod(tmp, 0o644)
        os.replace(tmp, p["path"])
    diff = deepops_diff.text(p["path"], dump(data), dump(record))
    module.exit_json(
        changed=changed, written=written, orphans=orphans, before=before, diff=diff if changed else {}
    )


if __name__ == "__main__":
    main()
