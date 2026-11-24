"""systemd_dropin: the one way a role writes or removes a drop-in of a systemd unit
(/etc/systemd/system/<unit>.d/<name>.conf), systemd reloaded when it changed.

copy and template never create the directory a file goes in, and no package ships a
unit's drop-in directory: job_scratch wrote slurmd's without it and failed on its first
host ("Destination directory /etc/systemd/system/slurmd.service.d does not exist",
2026-11-22), while six other roles each spelled out the directory, the file and the
reload their own way. Here a drop-in is one task:

  path:          the drop-in file, <directory>/<unit>.d/<name>.conf
  content:       its text (state present)
  state:         present (default), masked or absent. masked links the path to
                 /dev/null: a package's drop-in of the same name (under /usr/lib) is
                 then not applied, as systemd.unit(5) has it, with the package's
                 file left as shipped. absent also removes the directory once
                 nothing else is in it (a site's own drop-ins stay, and so does it)
  daemon_reload: true (default): systemd reloads when the file changed, so the next
                 start or restart (a handler the task notifies) sees it

The directory is 0755 and the file 0644, created by root. It takes copy's check and
diff: --check --diff shows the drop-in a run would write or remove.
"""

from __future__ import annotations

import os
import re

from ansible.errors import AnsibleActionFail
from ansible.plugins.action import ActionBase

UNIT_TYPES = "service|socket|slice|timer|mount|automount|swap|path|target|scope"
DROPIN = re.compile(rf"^/(?:[^/]+/)*[^/]+\.(?:{UNIT_TYPES})\.d/[^/]+\.conf$")
ARGS = {"path", "content", "state", "daemon_reload"}


def check(args):
    """The arguments, or the reason they are refused."""
    unknown = set(args) - ARGS
    if unknown:
        return f"unknown arguments: {', '.join(sorted(unknown))}"
    path, state = str(args.get("path", "")), args.get("state", "present")
    if not DROPIN.match(path):
        return f"path is a unit's drop-in, <directory>/<unit>.d/<name>.conf, not {path!r}"
    if state not in ("present", "masked", "absent"):
        return f"state is present, masked or absent, not {state!r}"
    if state == "present" and "content" not in args:
        return "state present needs content"
    if state != "present" and "content" in args:
        return f"state {state} takes no content"
    return ""


class ActionModule(ActionBase):
    def _module(self, name, args, task_vars):
        result = self._execute_module(
            module_name=f"ansible.legacy.{name}", module_args=args, task_vars=task_vars
        )
        if result.get("failed"):
            raise AnsibleActionFail(result.get("msg", f"{name} failed"), result=result)
        return result

    def _write(self, path, content, task_vars):
        """Copy's result for the file, after its directory is made."""
        made = self._module(
            "file", {"path": os.path.dirname(path), "state": "directory", "mode": "0755"}, task_vars
        )
        task = self._task.copy()
        task.args = {"dest": path, "content": content, "mode": "0644"}
        copy = self._shared_loader_obj.action_loader.get(
            "ansible.legacy.copy",
            task=task,
            connection=self._connection,
            play_context=self._play_context,
            loader=self._loader,
            templar=self._templar,
            shared_loader_obj=self._shared_loader_obj,
        )
        result = copy.run(task_vars=task_vars)
        result["changed"] = bool(result.get("changed") or made.get("changed"))
        return result

    def _mask(self, path, task_vars):
        """The path, after its directory is made, a link to /dev/null."""
        made = self._module(
            "file", {"path": os.path.dirname(path), "state": "directory", "mode": "0755"}, task_vars
        )
        result = self._module(
            "file", {"path": path, "src": "/dev/null", "state": "link", "force": True}, task_vars
        )
        result["changed"] = bool(result.get("changed") or made.get("changed"))
        return result

    def _remove(self, path, task_vars):
        """The file's removal, then its directory's when nothing else is in it."""
        result = self._module("file", {"path": path, "state": "absent"}, task_vars)
        directory = os.path.dirname(path)
        if not self._module("stat", {"path": directory, "get_checksum": False}, task_vars)["stat"].get(
            "isdir"
        ):
            return result
        # Under --check the file is still there; it counts as gone.
        left = self._module("find", {"paths": directory, "file_type": "any", "hidden": True}, task_vars)
        if not [f["path"] for f in left.get("files", []) if f["path"] != path]:
            self._module("file", {"path": directory, "state": "absent"}, task_vars)
            result["changed"] = True
            result["directory"] = f"removed {directory}"
        return result

    def run(self, tmp=None, task_vars=None):
        del tmp
        result = super().run(task_vars=task_vars)
        args = dict(self._task.args)
        refused = check(args)
        if refused:
            raise AnsibleActionFail(f"systemd_dropin: {refused}")
        path = str(args["path"])
        state = args.get("state", "present")
        if state == "present":
            result.update(self._write(path, str(args["content"]), task_vars))
        elif state == "masked":
            result.update(self._mask(path, task_vars))
        else:
            result.update(self._remove(path, task_vars))
        if result.get("failed"):
            return result
        if result.get("changed") and args.get("daemon_reload", True):
            if self._task.check_mode:
                result["daemon_reload"] = "would reload systemd"
            else:
                self._module("systemd_service", {"daemon_reload": True}, task_vars)
                result["daemon_reload"] = "reloaded systemd"
        return result
