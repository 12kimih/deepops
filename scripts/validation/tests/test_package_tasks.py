"""Every apt install refreshes the package cache it reads.

A freshly installed host has an empty apt cache, so an install without
update_cache fails there with "No package matching ... is available" (the
os_updates molecule run found this). An install either sets update_cache
(with cache_valid_time, so a warm cache costs nothing; or an expression that
is true when it just added a repository) or directly follows a task that
refreshes the cache.
"""
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]


def refreshes(task):
    apt = task.get("ansible.builtin.apt") if isinstance(task, dict) else None
    # true, or an expression such as "the repository just changed"
    return isinstance(apt, dict) and apt.get("update_cache") not in (None, False)


def installs_by_name(task):
    apt = task.get("ansible.builtin.apt") if isinstance(task, dict) else None
    return isinstance(apt, dict) and apt.get("name") and apt.get("state", "present") in ("present", "latest")


def task_lists(data):
    if not isinstance(data, list):
        return
    if data and isinstance(data[0], dict) and ("hosts" in data[0] or "import_playbook" in data[0]):
        for play in data:
            for key in ("pre_tasks", "tasks", "post_tasks", "handlers"):
                yield from task_lists(play.get(key))
        return
    yield data
    for task in data:
        if isinstance(task, dict):
            for key in ("block", "rescue", "always"):
                yield from task_lists(task.get(key))


def offenders():
    files = [p for p in sorted(ROOT.glob("roles/*/tasks/**/*.yml")) + sorted(ROOT.glob("playbooks/**/*.yml"))
             if "galaxy" not in p.parts]
    for path in files:
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError:
            continue
        for tasks in task_lists(data):
            for i, task in enumerate(tasks):
                if installs_by_name(task) and not refreshes(task) and not (i and refreshes(tasks[i - 1])):
                    yield f"{path.relative_to(ROOT)}: {task.get('name')}"


class PackageTaskTests(unittest.TestCase):
    def test_apt_installs_refresh_the_cache(self):
        found = list(offenders())
        self.assertEqual(found, [], "add update_cache: true and cache_valid_time: 3600 to:\n" + "\n".join(found))


if __name__ == "__main__":
    unittest.main()
