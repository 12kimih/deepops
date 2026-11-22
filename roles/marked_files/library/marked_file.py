#!/usr/bin/python
"""Remove files only where they show the deepops role made them."""

DOCUMENTATION = r"""
module: marked_file
short_description: Remove each path that shows deepops made it; leave the rest
description:
  - A file a role writes at a generic path (/etc/cron.allow, a polkit rule) may instead
    be a site's own, written by its own Ansible ("Ansible managed") too. Each path goes
    only when its name or text says deepops or names the role that wrote it
    (module_utils/deepops_marks.py: made_by); one without that mark is returned in C(kept).
  - Supports check mode.
options:
  paths:
    type: list
    elements: path
    required: true
  role:
    type: str
    required: true
    description: The role whose mark (roles/ROLE) the files carry.
"""

import os

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import deepops_diff
from ansible.module_utils.deepops_marks import made_by


def main():
    module = AnsibleModule(
        argument_spec={
            "paths": {"type": "list", "elements": "path", "required": True},
            "role": {"type": "str", "required": True},
        },
        supports_check_mode=True,
    )
    removed, kept = [], []
    for path in module.params["paths"]:
        if not os.path.lexists(path):
            continue
        if not made_by(path, module.params["role"]):
            kept.append(path)
            continue
        removed.append(path)
        if not module.check_mode:
            os.unlink(path)
    diff = deepops_diff.actions(f"remove {path}" for path in removed)
    module.exit_json(changed=bool(removed), removed=removed, kept=kept, diff=diff if removed else {})


if __name__ == "__main__":
    main()
