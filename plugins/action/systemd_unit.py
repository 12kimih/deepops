"""systemd_unit: ansible.builtin.systemd_service for every task that starts, stops,
enables or reloads a unit, previewed under --check even before the unit exists.

Under --check nothing is installed, so a unit a package or a template of the same run
brings does not exist yet, and systemd_service fails on it ("Could not find the
requested service"): fail2ban's preview failed on every node that did not have it yet
(2026-11), and so would any handler or later play that starts what this run installs.
systemd_unit takes systemd_service's arguments and runs exactly as it does; under
--check, for a unit that is not there yet, it reports what it would do once the run has
installed it as a change, and with --diff shows it as the diff.

A unit enabled already is enabled again, so the links its [Install] gained since are
made: ansible's systemd_service skips an enabled unit, so a container unit that gained
WantedBy=docker.service stayed down after Docker restarted (2026-11-22). systemctl
enable creates only the links that are missing and says so ("Created symlink"), which is
what reports the change; a name it refuses (an alias) is left as systemd_service set it.
"""

from __future__ import annotations

from ansible.plugins.action import ActionBase

MISSING = "Could not find the requested service"
STATES = {"started": "start", "stopped": "stop", "restarted": "restart", "reloaded": "reload"}


def describe(args):
    """What the task would do to a unit that does not exist yet, one step per line."""
    name = args.get("name") or "systemd"
    steps = ["reload systemd"] if args.get("daemon_reload") else []
    if args.get("masked") is not None:
        steps.append(f"{'mask' if args['masked'] else 'unmask'} {name}")
    if args.get("enabled") is not None:
        steps.append(f"{'enable' if args['enabled'] else 'disable'} {name}")
    if args.get("state") in STATES:
        steps.append(f"{STATES[args['state']]} {name}")
    return steps


def links_created(command_result):
    """The symlinks systemctl enable reported creating (none when it failed)."""
    if command_result.get("rc"):
        return []
    text = f"{command_result.get('stdout', '')}\n{command_result.get('stderr', '')}"
    return [line.strip() for line in text.splitlines() if line.strip().startswith("Created symlink")]


class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        del tmp
        result = super().run(task_vars=task_vars)
        args = dict(self._task.args)
        result.update(
            self._execute_module(
                module_name="ansible.legacy.systemd_service", module_args=args, task_vars=task_vars
            )
        )
        if (
            args.get("enabled") is True
            and args.get("name")
            and not result.get("failed")
            and not self._task.check_mode
        ):
            linked = self._execute_module(
                module_name="ansible.legacy.command",
                module_args={"argv": ["systemctl", "enable", args["name"]]},
                task_vars=task_vars,
            )
            if links_created(linked):
                result["changed"] = True
                result["links"] = links_created(linked)
        if self._task.check_mode and result.get("failed") and MISSING in str(result.get("msg", "")):
            # Stopping or disabling what is not there is already done.
            steps = [s for s in describe(args) if not s.startswith(("stop ", "disable "))]
            line = (
                f"{', '.join(steps)} once this run installs it" if steps else "nothing to do: not installed"
            )
            return {
                "changed": bool(steps),
                "msg": f"would {line}" if steps else line,
                "diff": {"prepared": "".join(f"would {s}\n" for s in steps)},
            }
        return result
