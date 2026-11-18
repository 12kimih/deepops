"""A tag inside an included task file is reachable.

include_tasks is dynamic: run with --tags, Ansible evaluates an include only
when the include itself matches, and the included tasks are then filtered by
their own tags. An untagged include of tagged tasks hides them, so
`--tags config` on the slurm role used to do nothing. An include of tagged
tasks carries tags itself (normally `always`).
"""
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
# Markers that look like tags but select nothing.
NOT_TAGS = {"always", "skip_ansible_lint"}


def load(path):
    try:
        return yaml.safe_load(path.read_text()) or []
    except yaml.YAMLError:
        return []


def tags_in(tasks):
    found = set()
    for task in tasks if isinstance(tasks, list) else []:
        if isinstance(task, dict):
            tags = task.get("tags") or []
            found |= {tags} if isinstance(tags, str) else set(tags)
            for key in ("block", "rescue", "always"):
                found |= tags_in(task.get(key))
    return found - NOT_TAGS


def hidden():
    for path in sorted(ROOT.glob("roles/*/tasks/**/*.yml")):
        if "galaxy" in path.parts:
            continue
        for task in load(path):
            target = task.get("ansible.builtin.include_tasks") if isinstance(task, dict) else None
            if not isinstance(target, str) or "{{" in target or task.get("tags"):
                continue
            inner = tags_in(load(path.parent / target)) if (path.parent / target).exists() else set()
            if inner:
                yield f"{path.relative_to(ROOT)}: include {target} hides {sorted(inner)}"


class TaskTagTests(unittest.TestCase):
    def test_included_tags_are_reachable(self):
        found = list(hidden())
        self.assertEqual(found, [], "tag these includes (tags: always):\n" + "\n".join(found))


if __name__ == "__main__":
    unittest.main()
