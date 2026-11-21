#!/usr/bin/python
"""Add a name-service source to /etc/nsswitch.conf, or take it away, leaving the rest."""

DOCUMENTATION = r"""
module: nsswitch_source
short_description: Add or remove one source (nis, sss) in nsswitch.conf, token by token
description:
  - Each directory client owns one source token, not whole lines, so two clients never
    fight over a line and one going leaves what the other set. A migration from NIS to
    SSSD replaces C(nis) with C(sss); removing the NIS client afterwards finds no C(nis)
    left and changes nothing.
  - present appends I(source) to each of I(databases) where it is missing (a missing
    line is added as C(<db>: files <source>)), drops it from every other line, and drops
    the I(replaces) tokens from every line. absent drops I(source) from every line. A
    line left empty reads C(files).
  - A token's C([STATUS=action]) criteria go with it. Other lines, comments and spacing
    are kept. Supports check mode and diff.
options:
  path:
    type: path
    default: /etc/nsswitch.conf
  source:
    type: str
    required: true
  databases:
    description: The databases that read I(source) (present only).
    type: list
    elements: str
    default: []
  replaces:
    description: Sources I(source) takes over from, dropped from every line (present only).
    type: list
    elements: str
    default: []
  state:
    type: str
    choices: [present, absent]
    default: present
"""

import os
import re
import tempfile

from ansible.module_utils import deepops_diff
from ansible.module_utils.basic import AnsibleModule

LINE = re.compile(r"^(?P<db>[a-z_]+)(?P<sep>:\s*)(?P<rest>[^#]*?)(?P<tail>\s*(#.*)?)$")


def _drop(tokens, names):
    """TOKENS without each of NAMES and the [criteria] that follow it."""
    out, skipping = [], False
    for token in tokens:
        if token.startswith("[") and skipping:
            continue
        skipping = token in names
        if not skipping:
            out.append(token)
    return out


def apply(text, source, databases=(), replaces=(), state="present"):
    """The new file text (a pure function, for the tests)."""
    lines, seen = text.splitlines(), set()
    for i, line in enumerate(lines):
        m = LINE.match(line)
        if not m:
            continue
        db, tokens = m["db"], m["rest"].split()
        if state == "present" and db in databases:
            # Where the source already is, it stays: only a missing one is appended.
            new = _drop(tokens, set(replaces))
            new += [] if source in new else [source]
        elif state == "present":
            new = _drop(tokens, set(replaces) | {source})
        else:
            new = _drop(tokens, {source})
        new = new or ["files"]
        seen.add(db)
        if new != tokens:
            lines[i] = f"{db}{m['sep']}{' '.join(new)}{m['tail']}"
    if state == "present":
        lines += [f"{db}: files {source}" for db in databases if db not in seen]
    return "\n".join(lines) + "\n"


def main():
    module = AnsibleModule(
        argument_spec={
            "path": {"type": "path", "default": "/etc/nsswitch.conf"},
            "source": {"type": "str", "required": True},
            "databases": {"type": "list", "elements": "str", "default": []},
            "replaces": {"type": "list", "elements": "str", "default": []},
            "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
        },
        supports_check_mode=True,
    )
    p = module.params
    try:
        with open(p["path"]) as f:
            before = f.read()
    except FileNotFoundError:
        before = ""
    after = apply(before, p["source"], p["databases"], p["replaces"], p["state"])
    changed = after != before
    if changed and not module.check_mode:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p["path"]))
        with os.fdopen(fd, "w") as f:
            f.write(after)
        os.chmod(tmp, 0o644)
        os.replace(tmp, p["path"])
    module.exit_json(changed=changed, diff=deepops_diff.text(p["path"], before, after) if changed else {})


if __name__ == "__main__":
    main()
