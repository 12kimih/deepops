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

The module is `use` (apt or dnf; default: the host's package manager, as
ansible.builtin.package finds it), which takes every other argument as given.
"""

from __future__ import annotations

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
        if self._task.check_mode and done.get("failed") and pending:
            done = preview(done, pending, args.get("name", []))
        result.update(done)
        return result
