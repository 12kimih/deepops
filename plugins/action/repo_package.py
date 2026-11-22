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
fails under --check with removals pending, repo_package asks apt about the host as the
run leaves it: apt-get simulates the install against a copy of dpkg's status without
the removed packages (-o Dir::State::status). One transaction that removes and installs
is not the run: apt resolves it as a whole and refused what the run's removal, then its
install, does (an i386 library holding its amd64 twin, 2026-11). apt's answer is the
preview, a change when it would succeed, the failure (with apt's reason) when not.

An apt preview names what it would install (repo_package_installs, as apt's simulation
lists them), so a task after it can tell what the install brings (a kernel module, say)
rather than only that it changes something.

`refuse` (apt only: regular expressions on package names, with `refuse_reason`) keeps an
install from bringing what it must not, as apt would resolve it: before a run installs
anything, and in its preview, apt's simulation is read, and an install that would bring
a refused package fails naming it and the reason. A driver install that pulled a new
kernel onto some nodes only (the archive's prebuilt NVIDIA modules for the newer
kernel, 2026-11) is refused that way: kernels change on every node at once, in a
maintenance upgrade.

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


def simulation_argv(args):
    """What apt-get's simulation of the install takes after its options."""
    argv = ["install"]
    if args.get("install_recommends") is not None:
        argv.append("--install-recommends" if args["install_recommends"] else "--no-install-recommends")
    if args.get("default_release"):
        argv += ["-t", str(args["default_release"])]
    return argv + names_of(args.get("name"))


# dpkg's status without the paragraphs of `drop` ("name" for the native architecture or
# all, "name:arch" for that one): the host as the run's removal leaves it.
_WITHOUT = r"""
BEGIN { RS = ""; ORS = "\n\n"; "dpkg --print-architecture" | getline native
        n = split(drop, d, " "); for (i = 1; i <= n; i++) D[d[i]] = 1 }
{ p = ""; a = ""; m = split($0, L, "\n")
  for (i = 1; i <= m; i++) {
    if (L[i] ~ /^Package: /) p = substr(L[i], 10)
    if (L[i] ~ /^Architecture: /) a = substr(L[i], 15)
  }
  if ((p ":" a) in D || ((a == native || a == "all") && p in D)) next
  print }
"""


def simulation_command(args, removals, status="/var/lib/dpkg/status"):
    """A shell command: apt-get's simulation of the install on a host without REMOVALS."""
    simulate = shlex.join(["env", "LC_ALL=C", "apt-get", "-s", "-q"])
    if not removals:
        return f"{simulate} {shlex.join(simulation_argv(args))}"
    without = shlex.join(["awk", "-v", f"drop={' '.join(removals)}", _WITHOUT, status])
    return (
        "tmp=$(mktemp) && trap 'rm -f \"$tmp\"' EXIT && "
        f'{without} > "$tmp" && {simulate} -o "Dir::State::status=$tmp" {shlex.join(simulation_argv(args))}'
    )


def installs_of(simulation):
    """The packages apt's simulation would install or upgrade ("Inst NAME ...")."""
    return re.findall(r"(?m)^Inst (\S+)", simulation or "")


def apt_reason(*texts):
    """Why apt refuses, from its output: the unmet dependencies it lists and its E: lines.
    Over a pty (become with a password) apt's stderr arrives on stdout and the connection's
    own notices ("Shared connection to ... closed.") on stderr, so both are read."""
    reason, in_unmet = [], False
    for line in "\n".join(t for t in texts if t).splitlines():
        if "unmet dependencies" in line:
            in_unmet = True
        elif in_unmet and not line.startswith(" "):
            in_unmet = False  # the list is the indented lines after its heading
        if in_unmet or line.startswith(("E:", "W:")):
            reason.append(line.strip())
    return " ".join(reason) or "apt gave no reason"


def refused_in(installs, patterns):
    """The INSTALLS a pattern of PATTERNS (regular expressions, from the start) matches."""
    return [name for name in installs if any(re.match(p, name.split(":", 1)[0]) for p in patterns)]


def refusal(refused, reason):
    return {
        "failed": True,
        "changed": False,
        "msg": f"the install would bring {', '.join(refused)}, which this task refuses: {reason}",
        "repo_package_refused": refused,
    }


def with_removals(result, removals, simulated):
    """The preview of an install that follows removals the preview did not make."""
    rc, out, err = simulated.get("rc"), simulated.get("stdout", ""), simulated.get("stderr", "")
    if rc != 0:
        result = dict(result)
        result["msg"] = (
            f"{result.get('msg', '').strip()}; with {', '.join(removals)} removed first, as the run "
            f"does, apt still refuses: {apt_reason(out, err)}"
        )
        result["repo_package_simulation"] = {"rc": rc, "stdout": out, "stderr": err}
        return result
    installs = installs_of(out)
    return {
        "changed": bool(installs),
        "msg": f"would install {', '.join(installs) or 'nothing'} once the run removes {', '.join(removals)}",
        "diff": {"prepared": "".join(f"would install {name}\n" for name in installs)},
        "stdout": out,
        "repo_package_installs": installs,
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
        refuse, reason = list(args.pop("refuse", None) or []), args.pop("refuse_reason", "")
        if refuse and (use != "apt" or not reason):
            raise AnsibleActionFail("repo_package: refuse is for apt, and needs a refuse_reason")
        pending = list(task_vars.get("package_repos_unrefreshed") or [])
        removals = list(task_vars.get("host_baseline_unremoved") or [])
        if refuse and not self._task.check_mode:
            # Before anything is installed: apt's own answer to what the install brings,
            # from the package lists the install would read (refreshed first, if asked).
            if args.get("update_cache"):
                cache = {k: args.pop(k) for k in ("update_cache", "cache_valid_time") if k in args}
                refreshed = self._execute_module(
                    module_name=MODULES[use], module_args=cache, task_vars=task_vars
                )
                if refreshed.get("failed"):
                    result.update(refreshed)
                    return result
            simulated = self._simulate(args, [])
            refused = (
                refused_in(installs_of(simulated.get("stdout")), refuse) if simulated.get("rc") == 0 else []
            )
            if refused:
                result.update(refusal(refused, reason))
                return result
        done = self._execute_module(module_name=MODULES[use], module_args=args, task_vars=task_vars)
        if self._task.check_mode and done.get("failed") and pending:
            done = preview(done, pending, args.get("name", []))
        elif self._task.check_mode and done.get("failed") and removals and use == "apt":
            done = with_removals(done, removals, self._simulate(args, removals))
        elif self._task.check_mode and not done.get("failed") and use == "apt":
            done["repo_package_installs"] = installs_of(done.get("stdout"))
        refused = refused_in(done.get("repo_package_installs") or [], refuse)
        if refused:  # the preview of a run that refuses before installing
            done = {**refusal(refused, reason), "repo_package_installs": done["repo_package_installs"]}
        result.update(done)
        return result

    def _simulate(self, args, removals):
        """apt's simulation of the install on the host without REMOVALS (read-only)."""
        command = simulation_command(args, removals)
        return self._low_level_execute_command(f"/bin/sh -c {shlex.quote(command)}", sudoable=True)
