#!/usr/bin/python
"""Remove files only where they show deepops made them."""

DOCUMENTATION = r"""
module: marked_file
short_description: Remove each path that shows deepops made it; leave the rest
description:
  - A file a role writes at a generic path (/etc/cron.allow, a polkit rule) may instead
    be a site's own. Each path goes only when its name or text says a deepops role wrote
    it (module_utils/deepops_marks.py); one without the mark is returned in C(kept).
  - Supports check mode.
options:
  paths:
    type: list
    elements: path
    required: true
"""

import os

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.deepops_marks import made_by_deepops


def main():
    module = AnsibleModule(argument_spec={"paths": {"type": "list", "elements": "path", "required": True}},
                           supports_check_mode=True)  # fmt: skip
    removed, kept = [], []
    for path in module.params["paths"]:
        if not os.path.lexists(path):
            continue
        if not made_by_deepops(path):
            kept.append(path)
            continue
        removed.append(path)
        if not module.check_mode:
            os.unlink(path)
    module.exit_json(changed=bool(removed), removed=removed, kept=kept)


if __name__ == "__main__":
    main()
