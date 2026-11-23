"""Packages leave a host one way: host_baseline's absent tasks, then nothing keeps running.

A NIS server removed with a bare package task left its rc package (init scripts kept)
and its daemons running from the deleted binaries, still answering on the network.
Every removal now goes through roles/host_baseline/tasks/absent.yml, which purges and
then stops each system service whose main process runs a removed program
(files/orphan-services). Only the main process counts: a session inside ssh.service
or a job inside slurmd running a deleted file must never stop those.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("packages_filter", ROOT / "plugins" / "filter" / "packages.py")
_packages = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_packages)
packages_protected_taken = _packages.packages_protected_taken

sys.path.insert(0, str(ROOT / "tests" / "lib"))
import bounds  # noqa: E402
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
                  "_host_baseline_recorded": ["podman", "nvidia-driver-*"]}  # fmt: skip
        argv = render(self.TASKS[read]["ansible.builtin.command"]["argv"], values)
        self.assertEqual(argv[4:], ["libopengl0", "munge", "podman"])

    def test_a_recorded_pattern_protects_what_it_matches(self):
        # Removing xserver-xorg-core takes the NVIDIA driver, which a component records as
        # nvidia-driver-*: compared by exact name it went unrefused.
        removal = ["xserver-xorg-core:amd64", "nvidia-driver-580-server-open:amd64", "libfoo1:amd64"]
        self.assertEqual(packages_protected_taken(removal, ["xserver-xorg-core"], ["sudo", "nvidia-driver-*"]),
                         ["nvidia-driver-580-server-open"])  # fmt: skip

    def test_an_absent_pattern_matching_a_protected_package_is_refused(self):
        self.assertEqual(packages_protected_taken(["linux-image-6.8.0-1-generic"], ["linux-image-*"],
                                                  ["linux-image-*"]), ["linux-image-6.8.0-1-generic"])  # fmt: skip

    def test_a_protected_package_named_whole_goes(self):
        # A component's own removal names its package: a decision, not an accident.
        self.assertEqual(packages_protected_taken(["systemd-coredump:amd64"], ["systemd-coredump"],
                                                  ["systemd-coredump"]), [])  # fmt: skip

    def test_the_refusal_goes_through_the_filter(self):
        task = next(t for t in walk(yaml.safe_load(ABSENT.read_text()))
                    if t.get("name") == "Refuse a removal that takes a protected package with it")  # fmt: skip
        self.assertIn(
            "packages_protected_taken(host_baseline_absent_packages,", task["vars"]["host_baseline_taken"]
        )
        self.assertIn(
            "host_baseline_protected_packages + _host_baseline_recorded", task["vars"]["host_baseline_taken"]
        )

    def test_nothing_is_marked_or_installed_when_all_are_manual(self):
        values = {**self.facts(["ii  libc6:amd64"], []), "host_baseline_present": ["gdm3"],
                  "host_baseline_unmarked": self.unmarked(["ii  libc6:amd64"], [])}  # fmt: skip
        self.assertFalse(condition(self.TASKS[self.MARK]["when"], values))

    def test_the_plan_sees_what_was_marked_without_upgrading_it(self):
        # A preview marked nothing: both simulations read apt's extended_states without
        # the marked packages' paragraphs (absent = manual). "<name>+" upgraded them.
        hb_vars = yaml.safe_load((ROOT / "roles/host_baseline/vars/main.yml").read_text())
        awk = (ROOT / "plugins/apt/paragraphs-without.awk").read_text()
        values = {"host_baseline_present": ["gdm3"], "host_baseline_unmarked": ["libopengl0", "libgl1"],
                  "_host_baseline_simulate": hb_vars["_host_baseline_simulate"], "_host_baseline_without_awk": awk,
                  "_host_baseline_simulation": hb_vars["_host_baseline_simulation"]}  # fmt: skip
        for name, tail in ((self.PLAN, ["purge", "--autoremove", "gdm3"]),
                           ("Note what was already unneeded (Debian)", ["autoremove"])):  # fmt: skip
            argv = render(self.TASKS[name]["ansible.builtin.command"]["argv"], values)
            self.assertEqual(argv[:2], ["sh", "-c"], name)
            self.assertEqual(argv[3:6], ["simulate", "libopengl0 libgl1", awk], name)
            self.assertEqual(argv[6:], tail, name)
            self.assertNotIn("+", " ".join(argv[6:]), name)
        self.assertIn("Dir::State::extended_states=", hb_vars["_host_baseline_simulate"])

    def test_the_awk_drops_exactly_the_named_paragraphs(self):
        states = "Package: libopengl0\nArchitecture: amd64\nAuto-Installed: 1\n\n" \
                 "Package: zlib1g\nArchitecture: amd64\nAuto-Installed: 1\n\n" \
                 "Package: libgl1\nArchitecture: i386\nAuto-Installed: 1\n"  # fmt: skip
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "extended_states").write_text(states)
            out = subprocess.run(
                ["awk", "-v", "drop=libopengl0 libgl1", (ROOT / "plugins/apt/paragraphs-without.awk").read_text(),
                 str(Path(tmp, "extended_states"))],
                capture_output=True, text=True, check=True, timeout=bounds.CHILD,
            ).stdout  # fmt: skip
        self.assertNotIn("libopengl0", out)
        self.assertIn("zlib1g", out)
        self.assertIn("libgl1", out, "an i386 copy stays unless named name:i386")


class SnapsBeforeSnapd(unittest.TestCase):
    """snapd's purge failed on a snap mount it could not take down and left snapd half
    removed (2026-11-22): the snaps go through snapd first, then any mount left."""

    SNAPS = ROOT / "roles" / "host_baseline" / "tasks" / "snaps.yml"
    TASKS = {t.get("name"): t for t in walk(yaml.safe_load(SNAPS.read_text()))}
    LISTED = [
        "bare      1.0       5     latest/stable  canonical**  base",
        "core24    20260824  2124  latest/stable  canonical**  base",
        "firefox   146.0     9171  latest/stable  mozilla**    -",
        "snapd     2.78      25935 latest/stable  canonical**  snapd",
        "htop      3.4       6664  latest/stable  maxiberta    -",
    ]

    def test_applications_go_first_then_bases_then_snapd(self):
        task = self.TASKS["Remove each snap through snapd, applications first"]
        order = render(task["loop"], {**task["vars"], "host_baseline_snaps": {"stdout_lines": self.LISTED}})
        self.assertEqual(order, ["firefox", "htop", "bare", "core24", "snapd"])
        # What runs is each snap's name, never its whole line (2026-11-23: every removal
        # failed with "not found", the line given as the name).
        argv = [render(task["change_command"]["argv"], {"item": name}) for name in order]
        self.assertEqual(argv[0], ["snap", "remove", "--purge", "firefox"])

    def test_it_runs_before_the_removal_and_only_for_snapd(self):
        # The block and its removal task share the name: the task is the last of them.
        names = [t.get("name") for t in walk(yaml.safe_load(ABSENT.read_text()))]
        removal = len(names) - 1 - names[::-1].index("Remove the packages declared absent")
        self.assertLess(names.index("Take the snaps out before snapd"), removal)
        when = NeededPackagesStay.TASKS["Take the snaps out before snapd"]["when"]
        self.assertTrue(condition(when, {"host_baseline_removal": ["gdm3", "snapd"]}))
        self.assertTrue(condition(when, {"host_baseline_removal": ["snapd:amd64"]}))
        self.assertFalse(condition(when, {"host_baseline_removal": ["gdm3", "snapd-glib"]}))

    def test_only_snap_mounts_are_unmounted_deepest_first(self):
        task = self.TASKS["Unmount what is still mounted from /snap or /var/snap"]
        mounts = [
            "/",
            "/snap/firefox/9171",
            "/var/snap/firefox/common/host-hunspell",
            "/snapshots",
            "/var/snap",
            "/home",
        ]
        got = render(task["loop"], {"host_baseline_snap_mounts": {"stdout_lines": mounts}})
        self.assertEqual(got, ["/var/snap/firefox/common/host-hunspell", "/var/snap", "/snap/firefox/9171"])


class AbsentStaysOut(unittest.TestCase):
    """The site's absent list is pinned out after the removal, every architecture."""

    MAIN = ROOT / "roles" / "host_baseline" / "tasks" / "main.yml"
    TASKS = [t for t in walk(yaml.safe_load(MAIN.read_text()))]

    def test_the_pin_follows_the_removal_and_names_every_architecture(self):
        names = [t.get("name") for t in self.TASKS]
        pin = "Keep the packages declared absent out (Debian)"
        self.assertLess(names.index("Remove the packages declared absent"), names.index(pin))
        content = next(t for t in self.TASKS if t.get("name") == pin)["ansible.builtin.copy"]["content"]
        got = render(content, {"host_baseline_absent_packages": ["snapd", "gnome-*", "apache2*"]})
        self.assertIn("Package: snapd:any gnome-*:any apache2*:any\n", got)
        self.assertIn("Pin-Priority: -1", got)


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
