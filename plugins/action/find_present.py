"""find_present: ansible.builtin.find over the paths that exist; a path that does not is
an empty directory, not a warning.

find warns "Skipped '<path>' path due to this access issue: ... is not a directory" for
every path not on the host: a component's removal (prometheus' alert rules) looks on
every host that never had it, and a --check run looks into directories the same run
would make, so each deploy printed a warning per host for what is simply nothing to
find. find_present takes find's arguments, asks the host which of the paths are
directories, and runs find on those alone; with none, it returns find's empty result.
"""

from __future__ import annotations

from ansible.plugins.action import ActionBase


def path_list(paths):
    """find's `paths`: a list, or a comma-separated string."""
    if isinstance(paths, str):
        return [p.strip() for p in paths.split(",") if p.strip()]
    return [str(p) for p in paths or []]


class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        del tmp
        result = super().run(task_vars=task_vars)
        args = dict(self._task.args)
        present = []
        for path in path_list(args.get("paths")):
            stat = self._execute_module(
                module_name="ansible.legacy.stat",
                module_args={"path": path, "follow": True, "get_checksum": False},
                task_vars=task_vars,
            )
            if stat.get("stat", {}).get("isdir"):
                present.append(path)
        if not present:
            result.update({"changed": False, "files": [], "matched": 0, "examined": 0, "skipped_paths": {}})
            return result
        args["paths"] = present
        result.update(
            self._execute_module(module_name="ansible.legacy.find", module_args=args, task_vars=task_vars)
        )
        return result
