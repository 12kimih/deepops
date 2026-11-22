"""repo_package: install packages with apt or dnf, previewing under --check what comes
from a repository the same run adds.

roles/package_repos adds a repository under --check without writing it or refreshing
the package index, so apt and dnf do not know its packages yet, and the install that
follows failed every preview of a first deploy (Slurm, Docker, the CUDA and NVIDIA
container toolkits, Open OnDemand, MOFED, a dnf module stream). package_repos notes
each repository a preview adds that way (package_repos_unrefreshed); repo_package runs
the package manager's module with the task's arguments, and when that fails under
--check while such a repository is pending, it reports the install as the change it
will be, the module's message beside it. A real run, or a preview with no repository
pending, is exactly the module's.

A preview removes nothing either: roles/host_baseline (tasks_from absent) notes what
the run would remove (host_baseline_unremoved), and an apt install the removal makes
possible (another driver's leftovers in the way) failed its preview. When an apt install
fails under --check with removals pending, repo_package asks apt about the run's real
order in one simulated transaction, `apt-get -s install <names> <removed>-`: apt's
answer is the preview, a change when it would succeed, the failure (with apt's reason)
when it would not.

The module is `use` (apt or dnf; default: the host's package manager, as
ansible.builtin.package finds it), which takes every other argument as given.
"""

from __future__ import annotations

import re
import shlex

from ansible.errors import AnsibleActionFail
from ansible.plugins.action import ActionBase

MODULES = {"apt": "ansible.legacy.apt", "dnf": "ansible.legacy.dnf", "dnf5": "ansible.legacy.dnf5"}


def preview(result, pending, names):
    """The failed preview of an install from a repository not yet indexed, as a change."""
    shown = ", ".join(str(n) for n in names) if isinstance(names, list) else str(names)
    return {
        "changed": True,
        "msg": (
            f"would install {shown} once {', '.join(pending)} is added (the preview cannot "
            f"read its packages yet: {result.get('msg', '').strip()})"
        ),
        "diff": {"prepared": f"would install {shown} from {', '.join(pending)}\n"},
        "repo_package_pending": pending,
    }


def names_of(names):
    """The apt module's `name`: a list, or a comma-separated string."""
    if isinstance(names, str):
        return [n.strip() for n in names.split(",") if n.strip()]
    return [str(n) for n in names or []]


def simulation_argv(args, removals):
    """apt-get's simulation of the install with the pending removals in one transaction."""
    argv = ["apt-get", "-s", "-q", "install"]
    if args.get("install_recommends") is not None:
        argv.append("--install-recommends" if args["install_recommends"] else "--no-install-recommends")
    if args.get("default_release"):
        argv += ["-t", str(args["default_release"])]
    return argv + names_of(args.get("name")) + [f"{package}-" for package in removals]


def with_removals(result, removals, simulated):
    """The preview of an install that follows removals the preview did not make."""
    rc, out, err = simulated.get("rc"), simulated.get("stdout", ""), simulated.get("stderr", "")
    if rc != 0:
        result = dict(result)
        result["msg"] = (
            f"{result.get('msg', '').strip()}; with {', '.join(removals)} removed first, as the run "
            f"does, apt still refuses: {(err or out).strip()}"
        )
        return result
    installs = re.findall(r"(?m)^Inst (\S+)", out)
    return {
        "changed": bool(installs),
        "msg": f"would install {', '.join(installs) or 'nothing'} once the run removes {', '.join(removals)}",
        "diff": {"prepared": "".join(f"would install {name}\n" for name in installs)},
        "stdout": out,
        "repo_package_after_removal": removals,
    }


class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        del tmp
        result = super().run(task_vars=task_vars)
        task_vars = task_vars or {}
        args = dict(self._task.args)
        use = args.pop("use", None) or task_vars.get("ansible_facts", {}).get("pkg_mgr")
        if not use:  # facts not gathered: ask the host, as ansible.builtin.package does
            facts = self._execute_module(
                module_name="ansible.legacy.setup",
                module_args={"gather_subset": ["!all"], "filter": ["ansible_pkg_mgr"]},
                task_vars=task_vars,
            )
            use = facts.get("ansible_facts", {}).get("ansible_pkg_mgr")
        if use not in MODULES:
            raise AnsibleActionFail(f"repo_package: use must be one of {', '.join(MODULES)}, not {use!r}")
        done = self._execute_module(module_name=MODULES[use], module_args=args, task_vars=task_vars)
        pending = list(task_vars.get("package_repos_unrefreshed") or [])
        removals = list(task_vars.get("host_baseline_unremoved") or [])
        if self._task.check_mode and done.get("failed") and pending:
            done = preview(done, pending, args.get("name", []))
        elif self._task.check_mode and done.get("failed") and removals and use == "apt":
            argv = simulation_argv(args, removals)
            simulated = self._low_level_execute_command("LC_ALL=C " + shlex.join(argv), sudoable=True)
            done = with_removals(done, removals, simulated)
        result.update(done)
        return result
