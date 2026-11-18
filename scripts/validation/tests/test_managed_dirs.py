"""A set of files installed into a directory goes through roles/managed_dir.

A copy or template looped over a glob only adds: a file dropped from the sources stays
on the host, and in a prolog.d, a rule directory or a Python package it keeps running.
The glob also reads the controller's working tree, so whatever lies there ships too
(the clustertools package carried a __pycache__ this way). managed_dir installs the set
and removes everything else. A directory the role does not own alone is the exception,
listed here with the reason.
"""

from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
INSTALLERS = ("ansible.builtin.copy", "ansible.builtin.template", "copy", "template")

# file -> why a glob install there must not remove what it did not install
SHARED = {
    "roles/facts/tasks/main.yml": "/etc/ansible/facts.d is Ansible's host-wide fact directory; "
    "other tools and admins put facts there too",
}


def tasks(data):
    if not isinstance(data, list):
        return
    for task in data:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("block", "rescue", "always", "tasks", "pre_tasks", "post_tasks", "handlers"):
            yield from tasks(task.get(key))


def globbed_installs(path):
    for task in tasks(yaml.safe_load(path.read_text()) or []):
        if not any(k in task for k in INSTALLERS):
            continue
        loop = str(task.get("loop", "")) + str(task.get("with_items", ""))
        if "with_fileglob" in task or "fileglob" in loop:
            yield task.get("name", "?")


class ManagedDirTests(unittest.TestCase):
    def test_globbed_installs_go_through_managed_dir(self):
        found = {}
        for path in sorted(ROOT.glob("roles/*/tasks/*.yml")) + sorted(ROOT.glob("playbooks/**/*.yml")):
            rel = str(path.relative_to(ROOT))
            names = list(globbed_installs(path))
            if names and rel not in SHARED:
                found[rel] = names
        self.assertEqual(found, {}, "install these through roles/managed_dir (see its tasks/main.yml)")

    def test_exceptions_still_exist(self):
        for rel in SHARED:
            self.assertTrue(list(globbed_installs(ROOT / rel)), f"{rel} no longer globs; drop it from SHARED")


if __name__ == "__main__":
    unittest.main()
