"""Packages leave a host one way: host_baseline's absent tasks, then nothing keeps running.

A NIS server removed with a bare package task left its rc package (init scripts kept)
and its daemons running from the deleted binaries, still answering on the network.
Every removal now goes through roles/host_baseline/tasks/absent.yml, which purges and
then stops each system service whose main process runs a removed program
(files/orphan-services). Only the main process counts: a session inside ssh.service
or a job inside slurmd running a deleted file must never stop those.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import condition, render  # noqa: E402
from repo_scan import ansible_files  # noqa: E402

SCRIPT = ROOT / "roles" / "host_baseline" / "files" / "orphan-services"
ABSENT = ROOT / "roles" / "host_baseline" / "tasks" / "absent.yml"
PACKAGE_MODULES = {
    f"{prefix}{m}" for prefix in ("", "ansible.builtin.") for m in ("apt", "dnf", "yum", "package")
}


def walk(tasks):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("block", "rescue", "always"):
            yield from walk(task.get(key))


class OrphanServices(unittest.TestCase):
    def proc(self, entries, main=None):
        """A fake /proc of (exe, cgroup[, parent pid]) entries from pid 100 on (parent 1
        unless given), and a fake systemctl that reports `main` ({unit: pid}) as each
        unit's MainPID (default: pid 100)."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(root)])
        for pid, (exe, cgroup, *parent) in enumerate(entries, start=100):
            d = root / str(pid)
            d.mkdir()
            os.symlink(exe, d / "exe")
            (d / "cgroup").write_text(cgroup + "\n")
            (d / "status").write_text(f"Name:\tx\nPPid:\t{parent[0] if parent else 1}\n")
        cases = "".join(f"  {unit}) echo {pid} ;;\n" for unit, pid in (main or {}).items())
        stub = root / "systemctl"
        stub.write_text(f'#!/bin/sh\ncase "$4" in\n{cases}  *) echo 100 ;;\nesac\n')
        stub.chmod(0o755)
        return root

    def run_script(self, root):
        env = {**os.environ, "SYSTEMCTL": str(root / "systemctl")}
        return subprocess.run(
            [str(SCRIPT), str(root)], capture_output=True, text=True, check=True, env=env
        ).stdout

    def test_reports_a_service_whose_program_is_gone(self):
        root = self.proc([("/nonexistent/sbin/rpc.ypxfrd (deleted)", "0::/system.slice/ypxfrd.service")])
        self.assertEqual(self.run_script(root), "ypxfrd.service /nonexistent/sbin/rpc.ypxfrd\n")

    def test_cgroup_v1_layout(self):
        root = self.proc([("/nonexistent/x (deleted)", "1:name=systemd:/system.slice/x.service")])
        self.assertEqual(self.run_script(root), "x.service /nonexistent/x\n")

    def test_leaves_an_upgraded_daemon(self):
        # The file was replaced, not removed: the path exists again.
        root = self.proc([(f"{SCRIPT} (deleted)", "0::/system.slice/upgraded.service")])
        self.assertEqual(self.run_script(root), "")

    def test_never_touches_users_or_jobs(self):
        root = self.proc(
            [
                ("/nonexistent/a.out (deleted)", "0::/user.slice/user-1000.slice/session-1.scope"),
                ("/nonexistent/train (deleted)", "0::/system.slice/slurmstepd.scope/job_1/step_0"),
                ("/nonexistent/c (deleted)", "0::/system.slice/docker-abc.scope"),
            ]
        )
        self.assertEqual(self.run_script(root), "")

    def test_a_session_process_never_stops_its_service(self):
        # An admin's process in ssh.service (no pam_systemd) or a job in slurmd.service
        # (cgroup v1) runs a deleted file; the daemon itself (pid 100) is fine.
        root = self.proc(
            [
                ("/usr/sbin/sshd", "0::/system.slice/ssh.service"),
                ("/nonexistent/tool (deleted)", "0::/system.slice/ssh.service"),
            ],
            main={"ssh.service": 100},
        )
        self.assertEqual(self.run_script(root), "")

    def test_a_forked_daemon_systemd_has_no_main_pid_for(self):
        # slapd from its init script: MainPID 0; the daemon's parent is init.
        root = self.proc(
            [("/nonexistent/slapd (deleted)", "0::/system.slice/slapd.service")], main={"slapd.service": 0}
        )
        self.assertEqual(self.run_script(root), "slapd.service /nonexistent/slapd\n")

    def test_without_a_main_pid_a_child_never_counts(self):
        root = self.proc(
            [("/nonexistent/tool (deleted)", "0::/system.slice/ssh.service", 4242)], main={"ssh.service": 0}
        )
        self.assertEqual(self.run_script(root), "")

    def test_a_systemd_inside_a_container_cgroup(self):
        # cgroupns=host: the host's services sit under the container's scope.
        root = self.proc(
            [("/nonexistent/x (deleted)", "0::/system.slice/docker-1.scope/system.slice/x.service")]
        )
        self.assertEqual(self.run_script(root), "x.service /nonexistent/x\n")

    def test_leaves_running_programs(self):
        root = self.proc([("/usr/sbin/sshd", "0::/system.slice/ssh.service")])
        self.assertEqual(self.run_script(root), "")


class NeededPackagesStay(unittest.TestCase):
    """What the host needs is marked installed by name before a removal is planned, so a
    desktop's purge cannot autoremove libopengl0 whichever role ran first (2026-11-22)."""

    TASKS = {t.get("name"): t for t in walk(yaml.safe_load(ABSENT.read_text()))}
    NAMES = list(TASKS)
    MARK = "Mark them installed by name (Debian)"
    UNMARKED = "Note the needed packages still marked automatic (Debian)"
    PLAN = "Work out what removing them would take with them (Debian)"

    def facts(self, needed, auto):
        return {"ansible_facts": {"os_family": "Debian"},
                "host_baseline_needed": {"stdout_lines": needed}, "host_baseline_auto": {"stdout_lines": auto}}  # fmt: skip

    def unmarked(self, needed, auto):
        return render(self.TASKS[self.UNMARKED]["ansible.builtin.set_fact"]["host_baseline_unmarked"],
                      self.facts(needed, auto))  # fmt: skip

    def test_the_mark_comes_before_the_plan(self):
        self.assertLess(
            self.NAMES.index(self.MARK), self.NAMES.index("Note what was already unneeded (Debian)")
        )
        self.assertLess(self.NAMES.index(self.MARK), self.NAMES.index(self.PLAN))
        self.assertIn("change_command", self.TASKS[self.MARK], "a preview shows the mark")

    def test_only_installed_automatic_needed_packages_are_marked(self):
        needed = ["ii  libopengl0:amd64", "ii  libc6:amd64", "un  libgl1", "rc  linux-image-6.8.0-1-generic"]
        self.assertEqual(self.unmarked(needed, ["libopengl0", "libgl1", "zlib1g"]), ["libopengl0"])

    def test_only_names_given_whole_are_read_for_marking(self):
        # linux-image-* also matches old kernels, which must stay automatic for apt.
        read = "Read which packages the host needs are installed (Debian)"
        values = {"host_baseline_protected_packages": ["libopengl0", "linux-image-*", "slurm-smd*", "munge"],
                  "_host_baseline_recorded": ["podman"]}  # fmt: skip
        argv = render(self.TASKS[read]["ansible.builtin.command"]["argv"], values)
        self.assertEqual(argv[4:], ["libopengl0", "munge", "podman"])

    def test_nothing_is_marked_or_installed_when_all_are_manual(self):
        values = {**self.facts(["ii  libc6:amd64"], []), "host_baseline_present": ["gdm3"],
                  "host_baseline_unmarked": self.unmarked(["ii  libc6:amd64"], [])}  # fmt: skip
        self.assertFalse(condition(self.TASKS[self.MARK]["when"], values))

    def test_the_plan_keeps_what_was_marked(self):
        values = {"host_baseline_present": ["gdm3"], "host_baseline_unmarked": ["libopengl0"]}
        argv = render(self.TASKS[self.PLAN]["ansible.builtin.command"]["argv"], values)
        self.assertEqual(
            argv[-2:], ["gdm3", "libopengl0+"], "kept as if installed by name, even in a preview"
        )


class OneRemovalPath(unittest.TestCase):
    def test_only_host_baseline_removes_packages(self):
        offenders = []
        for path in ansible_files():
            if path.parts[-3:-1] == ("host_baseline", "tasks"):
                continue
            data = yaml.safe_load(path.read_text())
            if not isinstance(data, list):
                continue
            plays = [p for p in data if isinstance(p, dict) and "hosts" in p]
            lists = (
                [p.get(k) for p in plays for k in ("pre_tasks", "tasks", "post_tasks")] if plays else [data]
            )
            for tasks in lists:
                for task in walk(tasks):
                    for module in PACKAGE_MODULES & task.keys():
                        args = task[module]
                        if isinstance(args, dict) and args.get("state") == "absent":
                            offenders.append(f"{path.relative_to(ROOT)}: {task.get('name')}")
        self.assertEqual(offenders, [], "remove packages with include_role host_baseline, tasks_from: absent")


if __name__ == "__main__":
    unittest.main()
