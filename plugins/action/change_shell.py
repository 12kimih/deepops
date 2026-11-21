"""change_shell: ansible.builtin.shell for a task that changes the host, shown by --check
instead of skipped. It is change_command run through the shell, as shell is to command
(see change_command.py)."""

from __future__ import annotations

from ansible.plugins.action import ActionBase


class ActionModule(ActionBase):
    _supports_async = True

    def run(self, tmp=None, task_vars=None):
        del tmp  # tmp no longer has any effect
        self._task.args["_uses_shell"] = True
        command = self._shared_loader_obj.action_loader.get(
            "change_command",
            task=self._task,
            connection=self._connection,
            play_context=self._play_context,
            loader=self._loader,
            templar=self._templar,
            shared_loader_obj=self._shared_loader_obj,
        )
        return command.run(task_vars=task_vars)
