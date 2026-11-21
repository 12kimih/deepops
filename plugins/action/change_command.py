"""change_command: ansible.builtin.command for a task that changes the host, shown by
--check instead of skipped.

Under --check Ansible skips command and shell, and display_skipped_hosts hides the skip,
so a preview reported changed=0 for changes the run then made (a BMC gateway, 2026-11).
change_command takes command's arguments and runs exactly as command does; under --check
it reports the command it would run as a change, and with --diff shows it as the diff.
It is always a change: a task that only reads uses command with changed_when: false.
With creates or removes, command itself predicts the outcome, so it decides.

change_shell (beside it) is the same for shell, as ansible.builtin.shell is to command.
"""

from __future__ import annotations

import shlex

from ansible.plugins.action import ActionBase


def describe(args):
    """The command line a task would run, as an operator would type it."""
    line = args.get("argv") or args.get("cmd") or args.get("_raw_params") or ""
    line = shlex.join(str(word) for word in line) if isinstance(line, list) else str(line).strip()
    return f"cd {shlex.quote(str(args['chdir']))} && {line}" if args.get("chdir") else line


class ActionModule(ActionBase):
    _supports_async = True

    def run(self, tmp=None, task_vars=None):
        del tmp  # tmp no longer has any effect
        args = self._task.args
        if self._task.check_mode and not {"creates", "removes"} & set(args):
            line = describe(args)
            return {
                "changed": True,
                "cmd": line,
                "msg": f"would run: {line}",
                "diff": {"prepared": f"would run: {line}\n"},
            }
        command = self._shared_loader_obj.action_loader.get(
            "ansible.legacy.command",
            task=self._task,
            connection=self._connection,
            play_context=self._play_context,
            loader=self._loader,
            templar=self._templar,
            shared_loader_obj=self._shared_loader_obj,
        )
        return command.run(task_vars=task_vars)
