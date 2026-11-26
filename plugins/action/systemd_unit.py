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

A task that enables a unit or reloads systemd then has systemd check the boot's
ordering (systemd-analyze verify default.target) and fails on a cycle, naming it. At
boot systemd breaks a cycle by deleting a start job: on two GPU nodes slurmd never
started (2026-11-26), from a power-cap unit still installed in its old form (After=
multi-user.target) and the power policy that orders itself after it and before slurmd.
A cycle may run through any unit on the host (a package's, one an older deploy left),
which no template test sees; this is where every role's units meet them.
"""

from __future__ import annotations

from ansible.plugins.action import ActionBase

MISSING = "Could not find the requested service"
# systemd-analyze's report of a cycle: the cycle, each unit on it, the job it drops.
CYCLE = ("Found ordering cycle", "Found dependency on", "to break ordering cycle")
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


def ordering_cycles(text):
    """systemd-analyze verify's lines that report an ordering cycle, in order."""
    return [line.strip() for line in text.splitlines() if any(mark in line for mark in CYCLE)]


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
        if (
            (args.get("enabled") is True or args.get("daemon_reload"))
            and not result.get("failed")
            and not self._task.check_mode
        ):
            verified = self._execute_module(
                module_name="ansible.legacy.command",
                module_args={"argv": ["systemd-analyze", "verify", "default.target"]},
                task_vars=task_vars,
            )
            cycles = ordering_cycles(f"{verified.get('stdout', '')}\n{verified.get('stderr', '')}")
            if cycles:
                result["failed"] = True
                result["cycles"] = cycles
                result["msg"] = (
                    "systemd's boot ordering has a cycle; at boot it drops a start job to break it, "
                    "so a unit on it would not start. Fix the After=/Before= of the units named: "
                    + " | ".join(cycles)
                )
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
