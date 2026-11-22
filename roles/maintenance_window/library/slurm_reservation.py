#!/usr/bin/python
"""Create, update or delete one Slurm reservation with scontrol."""

DOCUMENTATION = r"""
module: slurm_reservation
short_description: Converge one Slurm reservation with scontrol
description:
  - Reads the reservation I(name) with C(scontrol show reservation) and runs only the
    scontrol command that makes it match: create, update in place (times, users, nodes,
    flags; never delete and create, which would leave no window between) or delete.
    Supports check mode and diff mode.
options:
  name: {description: The reservation's name., type: str, required: true}
  state: {description: present or absent., type: str, default: present, choices: [present, absent]}
  start: {description: "Start time as scontrol takes it: YYYY-MM-DDTHH:MM[:SS] or now.", type: str}
  end: {description: "End time, YYYY-MM-DDTHH:MM[:SS].", type: str}
  nodes: {description: Nodes, or ALL., type: str, default: ALL}
  users: {description: Users allowed to run in it (comma-separated)., type: str, default: root}
  flags:
    description: Reservation flags; MAINT and IGNORE_JOBS make a maintenance window.
    type: list
    elements: str
    default: [MAINT, IGNORE_JOBS]
  scontrol: {description: Path of the scontrol binary., type: str, default: scontrol}
"""

import shlex

from ansible.module_utils import deepops_command, deepops_diff
from ansible.module_utils.basic import AnsibleModule

# Flags Slurm adds by itself (ALL_NODES for Nodes=ALL, SPEC_NODES for a node list).
IMPLIED = {"ALL_NODES", "SPEC_NODES"}


def parse(text):
    """{Key: value} of one `scontrol show reservation --oneliner` line, {} for none.
    Split on blanks: a value holding a space (a comment) misreads, but none of the fields
    read here (StartTime, EndTime, Nodes, Flags, Users) can hold one."""
    out = {}
    for token in text.split():
        key, sep, value = token.partition("=")
        if sep:
            out[key] = value
    return out


def seconds(t):
    """A time as scontrol prints it (seconds always there)."""
    return t if t in ("", "now") or t.count(":") == 2 else t + ":00"


def plan(want, have):
    """The scontrol argument list that makes HAVE ({} when absent) match WANT, or None."""
    name = want["name"]
    if want["state"] == "absent":
        return ["delete", f"ReservationName={name}"] if have else None
    fields = [f"StartTime={want['start']}", f"EndTime={want['end']}", f"Users={want['users']}"]
    create = ["create", "reservation", f"ReservationName={name}", *fields,
              f"Nodes={want['nodes']}", f"Flags={','.join(want['flags'])}"]  # fmt: skip
    if not have:
        return create
    # Changed in place, never deleted and created again: in between, no window would
    # hold the jobs back. Flags= replaces the whole set.
    flags = set(filter(None, have.get("Flags", "").split(","))) - IMPLIED
    if flags != set(want["flags"]):
        fields.append(f"Flags={','.join(want['flags'])}")
    nodes_ok = (
        ("ALL_NODES" in have.get("Flags", ""))
        if want["nodes"] == "ALL"
        else have.get("Nodes") == want["nodes"]
    )
    if not nodes_ok:
        fields.append(f"Nodes={want['nodes']}")
    changed = (
        len(fields) > 3
        or (want["start"] != "now" and have.get("StartTime") != seconds(want["start"]))
        or have.get("EndTime") != seconds(want["end"])
        or have.get("Users") != want["users"]
    )
    return ["update", f"ReservationName={name}", *fields] if changed else None


def main():
    module = AnsibleModule(
        argument_spec={
            "name": {"type": "str", "required": True},
            "state": {"type": "str", "default": "present", "choices": ["present", "absent"]},
            "start": {"type": "str"},
            "end": {"type": "str"},
            "nodes": {"type": "str", "default": "ALL"},
            "users": {"type": "str", "default": "root"},
            "flags": {"type": "list", "elements": "str", "default": ["MAINT", "IGNORE_JOBS"]},
            "scontrol": {"type": "str", "default": "scontrol"},
        },
        required_if=[("state", "present", ["start", "end"])],
        supports_check_mode=True,
    )
    p = module.params
    rc, out, err = deepops_command.run(
        module, [p["scontrol"], "show", "reservation", p["name"], "--oneliner"]
    )
    if rc != 0 and "not found" not in (out + err).lower():
        module.fail_json(msg=f"scontrol show reservation failed: {(err or out).strip()}")
    have = parse(out) if rc == 0 else {}
    cmd = plan({**p, "flags": [f.upper() for f in p["flags"]]}, have)
    steps = [cmd] if cmd else []
    lines = [shlex.join(["scontrol", *s]) for s in steps]
    result = {"changed": bool(steps), "commands": lines, "reservation": have}
    result["diff"] = deepops_diff.actions(lines) if steps else {}
    if not module.check_mode:
        for step in steps:
            rc, out, err = deepops_command.run(module, [p["scontrol"], *step])
            if rc != 0:
                module.fail_json(msg=f"scontrol {' '.join(step)} failed: {(err or out).strip()}", **result)
    module.exit_json(**result)


if __name__ == "__main__":
    main()
