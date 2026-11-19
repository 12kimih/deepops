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
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "roles" / "host_baseline" / "files" / "orphan-services"
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
        """A fake /proc of (exe, cgroup) entries from pid 100 on, and a fake systemctl
        that reports `main` ({unit: pid}) as each unit's MainPID (default: pid 100)."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(root)])
        for pid, (exe, cgroup) in enumerate(entries, start=100):
            d = root / str(pid)
            d.mkdir()
            os.symlink(exe, d / "exe")
            (d / "cgroup").write_text(cgroup + "\n")
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

    def test_leaves_running_programs(self):
        root = self.proc([("/usr/sbin/sshd", "0::/system.slice/ssh.service")])
        self.assertEqual(self.run_script(root), "")


class OneRemovalPath(unittest.TestCase):
    def test_only_host_baseline_removes_packages(self):
        offenders = []
        files = sorted(ROOT.glob("roles/*/tasks/**/*.yml")) + sorted(ROOT.glob("playbooks/**/*.yml"))
        for path in files:
            if "galaxy" in path.parts or path.parts[-3:-1] == ("host_baseline", "tasks"):
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
