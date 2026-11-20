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
        never removed: a file whose name or text says so (module_utils/deepops_marks.py);
        a unit whose unit file does; a container named deepops, or named as such a unit;
        a directory named deepops or holding an adopted file; a package named in
        I(exclusive_packages) (another role may install it unrecorded); an fstab entry as
        named (only explicit removals have them).
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
  version:
    description: The component's version, for C(begin) and C(present).
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
"""

import json
import os
import tempfile

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.deepops_marks import made_by_deepops, named_deepops

KINDS = ("package", "unit", "container", "file", "dir", "mount")


def load(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    data.setdefault("components", {})
    data.setdefault("items", {})
    return data


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


def apply(data, name, action, items, version, exclusive=(), evidence=lambda kind, ident: False):
    """The new record and the orphans (a pure function, for the tests)."""
    components = dict(data["components"])
    owners = {item: set(names) for item, names in data["items"].items()}
    before = components.get(name, {})
    if action == "begin":
        # A converged rerun (present at this version, every item already its own) leaves
        # the record as it is; new items or another version go through installing.
        converged = before == {"state": "present", "version": version} and all(
            name in owners.get(item, ()) for item in items
        )
        if not converged:
            components[name] = {"state": "installing", "version": version}
        for item in items:
            owners.setdefault(item, set()).add(name)
    elif action == "present":
        components[name] = {"state": "present", "version": version}
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
    grouped = {kind: [] for kind in KINDS}
    for item in orphans:
        kind, _, ident = item.partition(":")
        grouped[kind].append(ident)
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
        },
        supports_check_mode=True,
    )
    p = module.params
    bad = [i for i in p["items"] if i.partition(":")[0] not in KINDS or not i.partition(":")[2]]
    if bad:
        module.fail_json(msg=f"items must be KIND:ID with KIND one of {', '.join(KINDS)}: {bad}")
    data = load(p["path"])

    def evidence(kind, ident):
        if kind == "file":
            return made_by_deepops(ident)
        if kind == "dir":
            return named_deepops(ident)
        rc, out, _ = module.run_command(["systemctl", "show", "-P", "FragmentPath", ident])
        return rc == 0 and bool(out.strip()) and made_by_deepops(out.strip())

    record, orphans, before = apply(
        data, p["name"], p["action"], p["items"], p["version"], set(p["exclusive_packages"]), evidence
    )
    changed = record != data
    if changed and not module.check_mode:
        os.makedirs(os.path.dirname(p["path"]), mode=0o755, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p["path"]))
        with os.fdopen(fd, "w") as f:
            json.dump(record, f, indent=1, sort_keys=True)
            f.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, p["path"])
    module.exit_json(changed=changed, orphans=orphans, before=before)


if __name__ == "__main__":
    main()
