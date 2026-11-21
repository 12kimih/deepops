"""systemd_unit: ansible.builtin.systemd_service for every task that starts, stops,
enables or reloads a unit, previewed under --check even before the unit exists.

Under --check nothing is installed, so a unit a package or a template of the same run
brings does not exist yet, and systemd_service fails on it ("Could not find the
requested service"): fail2ban's preview failed on every node that did not have it yet
(2026-11), and so would any handler or later play that starts what this run installs.
systemd_unit takes systemd_service's arguments and runs exactly as it does; under
--check, for a unit that is not there yet, it reports what it would do once the run has
installed it as a change, and with --diff shows it as the diff.
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


class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        del tmp
        result = super().run(task_vars=task_vars)
        result.update(
            self._execute_module(
                module_name="ansible.legacy.systemd_service", module_args=self._task.args, task_vars=task_vars
            )
        )
        if self._task.check_mode and result.get("failed") and MISSING in str(result.get("msg", "")):
            # Stopping or disabling what is not there is already done.
            steps = [s for s in describe(self._task.args) if not s.startswith(("stop ", "disable "))]
            line = (
                f"{', '.join(steps)} once this run installs it" if steps else "nothing to do: not installed"
            )
            return {
                "changed": bool(steps),
                "msg": f"would {line}" if steps else line,
                "diff": {"prepared": "".join(f"would {s}\n" for s in steps)},
            }
        return result
