"""cluster_tools.io_usage: what each job and login is counted, from a stand-in /proc and
cgroup tree and a stand-in scontrol listpids. The jobs sit under SLUID-named cgroups, as
Slurm 26.05 lays them out: before, a job was found only by a job_<id> directory, so on
26.05 no job was ever counted (2026-11-27)."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
from cluster_tools import io_usage  # noqa: E402

UID = os.getuid()
NAME = io_usage.name_of(UID)


class Host:
    def __init__(self, d):
        self.d = Path(d)
        self.proc, self.cg, self.sys = self.d / "proc", self.d / "cg", self.d / "sys"
        for p in (self.proc, self.cg, self.sys, self.d / "bin"):
            p.mkdir()
        self.listed, self.scontrol = {}, self.d / "bin/scontrol"
        self.scontrol.write_text(
            f'#!/bin/sh\necho "$*" >> {self.d}/calls\n'
            f"[ -f {self.d}/listpids ] || {{ echo 'Unable to contact slurmstepd' >&2; exit 1; }}\n"
            f"cat {self.d}/listpids\n"
        )
        self.scontrol.chmod(0o755)

    def task(self, pid, job, rchar, wchar, **kw):
        """A task of job JOB, under its SLUID-named cgroup (sluid(JOB))."""
        self.pid(pid, STEP.format(sluid(job)), rchar, wchar, job=job, **kw)

    def listpids(self, up=True):
        """What scontrol listpids prints (the processes listed so far), or, UP false, a
        failing scontrol."""
        path = self.d / "listpids"
        if not up:
            path.unlink(missing_ok=True)
            return
        rows = ["PID      JOBID    STEPID   LOCALID GLOBALID"]
        rows += [f"{pid:<8} {job:<8} batch    0       0" for pid, job in self.listed.items()]
        path.write_text("\n".join(rows) + "\n")

    def calls(self):
        path = self.d / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def pid(self, pid, cgroup, rchar, wchar, uid=UID, ppid=1, start=100, job=None):
        """A process; JOB: the job id listpids gives it."""
        if job is not None:
            self.listed[str(pid)] = str(job)
            self.listpids()
        p = self.proc / str(pid)
        p.mkdir(exist_ok=True)
        (p / "stat").write_text(f"{pid} (a b) S {ppid} " + " ".join(["0"] * 17) + f" {start} 0 0\n")
        (p / "cgroup").write_text(f"0::{cgroup}\n")
        (p / "io").write_text(f"rchar: {rchar}\nwchar: {wchar}\nsyscr: 1\n")
        (p / "status").write_text(f"Name:\tx\nPPid:\t{ppid}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")

    def gone(self, pid):
        self.listed.pop(str(pid), None)
        for f in (self.proc / str(pid)).iterdir():
            f.unlink()
        (self.proc / str(pid)).rmdir()

    def cfg(self):
        return {"proc_root": str(self.proc), "cgroup_root": str(self.cg), "sys_root": str(self.sys),
                "slices": ["system.slice", "user.slice"], "textfile": str(self.d / "prom/io-usage.prom"),
                "state_file": str(self.d / "state/state.json"), "scontrol": str(self.scontrol)}  # fmt: skip

    def text(self):
        return (self.d / "prom/io-usage.prom").read_text()


STEP = "/system.slice/slurmstepd.scope/{}/step_0/user/task_0"


def sluid(job):
    """A SLUID-like name for JOB's cgroup: opaque to io_usage."""
    return f"s{job:013d}"


def series(job, user=NAME, cgroup=None):
    return f'job_id="{job}",cgroup="{cgroup or sluid(int(job))}",user="{user}"'


class Jobs(unittest.TestCase):
    def test_a_jobs_processes_are_summed_under_its_user_and_others_are_not_counted(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.task(10, 7, 100, 10)
            h.task(11, 7, 50, 5)
            h.pid(12, f"/system.slice/slurmstepd.scope/{sluid(7)}/step_0/slurm", 1, 1, uid=0)
            h.pid(13, "/user.slice/user-1000.slice/session-1.scope", 999, 999)
            h.task(14, 70, 3, 4)
            io_usage.run(h.cfg(), now=1000)
            text = h.text()
            self.assertIn(f"io_usage_job_read_bytes_total{{{series(7)}}} 151", text)
            self.assertIn(f"io_usage_job_written_bytes_total{{{series(7)}}} 16", text)
            self.assertIn(f"io_usage_job_read_bytes_total{{{series(70)}}} 3", text)
            self.assertNotIn("999", text)
            self.assertIn("io_usage_last_success_timestamp_seconds{} 1000", text)

    def test_the_counter_never_goes_back_when_a_process_takes_its_counts_away(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.task(10, 7, 100, 10)
            h.task(11, 7, 50, 5)
            io_usage.run(h.cfg())
            h.gone(11)  # a step ended, its counts went with slurmstepd
            h.task(10, 7, 120, 10)
            io_usage.run(h.cfg())
            self.assertIn(series(7), h.text())
            self.assertIn(f"io_usage_job_read_bytes_total{{{series(7)}}} 170", h.text())
            h.task(10, 7, 130, 10)
            io_usage.run(h.cfg())
            self.assertIn(f"io_usage_job_read_bytes_total{{{series(7)}}} 180", h.text())

    def test_a_child_reaped_into_its_parent_is_not_counted_twice(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.task(10, 7, 100, 10)
            h.task(11, 7, 50, 5, ppid=10)
            io_usage.run(h.cfg())
            h.gone(11)
            h.task(10, 7, 100 + 60, 10 + 5)  # the child's last 60/5 now in its parent
            io_usage.run(h.cfg())
            self.assertIn(f"io_usage_job_read_bytes_total{{{series(7)}}} 160", h.text())
            self.assertIn(f"io_usage_job_written_bytes_total{{{series(7)}}} 15", h.text())

    def test_a_reused_pid_is_another_process(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.task(10, 7, 100, 10, start=100)
            h.task(20, 7, 1, 1)
            io_usage.run(h.cfg())
            h.task(10, 7, 5, 0, start=200)  # pid 10 exited (parent outside) and came back
            io_usage.run(h.cfg())
            self.assertIn(f"io_usage_job_read_bytes_total{{{series(7)}}} 106", h.text())

    def test_a_finished_job_leaves_the_file_and_the_state(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.task(10, 7, 100, 10)
            io_usage.run(h.cfg())
            h.gone(10)
            io_usage.run(h.cfg())
            self.assertNotIn(sluid(7), h.text())
            self.assertEqual(json.loads(Path(h.cfg()["state_file"]).read_text()), {})

    def test_a_job_of_root_alone_is_roots(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.task(10, 8, 5, 5, uid=0)
            io_usage.run(h.cfg())
            self.assertIn(series(8, "root"), h.text())


class JobIds(unittest.TestCase):
    """Which job a job cgroup is comes from Slurm (scontrol listpids), never its name."""

    def test_the_job_id_layout_of_older_slurm_is_counted_the_same_way(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.pid(10, STEP.format("job_7"), 100, 10, job=7)
            io_usage.run(h.cfg())
            self.assertIn(f"io_usage_job_read_bytes_total{{{series(7, cgroup='job_7')}}} 100", h.text())

    def test_a_job_slurm_does_not_name_is_counted_with_no_id_and_said(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.pid(10, STEP.format(sluid(7)), 100, 10)  # listpids lists nothing for it
            h.task(11, 8, 5, 5)
            io_usage.run(h.cfg())
            text = h.text()
            self.assertIn(
                f'io_usage_job_read_bytes_total{{job_id="",cgroup="{sluid(7)}",user="{NAME}"}} 100', text
            )
            self.assertIn(f"io_usage_job_read_bytes_total{{{series(8)}}} 5", text)
            self.assertIn("io_usage_job_unmapped{} 1", text)

    def test_scontrol_failing_leaves_the_counts_and_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.task(10, 7, 100, 10)
            h.listpids(up=False)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(io_usage.run(h.cfg()), 0)
            self.assertIn(
                f'io_usage_job_read_bytes_total{{job_id="",cgroup="{sluid(7)}",user="{NAME}"}} 100', h.text()
            )
            self.assertIn("io_usage_job_unmapped{} 1", h.text())
            self.assertIn("Unable to contact slurmstepd", err.getvalue())

    def test_a_job_id_once_known_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.task(10, 7, 100, 10)
            io_usage.run(h.cfg())
            h.listpids(up=False)
            with contextlib.redirect_stderr(io.StringIO()):
                io_usage.run(h.cfg())
            self.assertIn(f"io_usage_job_read_bytes_total{{{series(7)}}} 100", h.text())
            self.assertIn("io_usage_job_unmapped{} 0", h.text())

    def test_listpids_is_asked_once_and_only_with_a_job_on_the_node(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.pid(13, "/user.slice/user-1000.slice/session-1.scope", 9, 9)
            io_usage.run(h.cfg())
            self.assertEqual(h.calls(), [])
            h.task(10, 7, 1, 1)
            h.task(11, 8, 1, 1)
            io_usage.run(h.cfg())
            self.assertEqual(h.calls(), ["listpids"])

    def test_listpids_is_read_by_its_header(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            (h.d / "listpids").write_text("JOBID PID STEPID LOCALID GLOBALID\n42 10 batch 0 0\n- 11 - - -\n")
            self.assertEqual(io_usage.listpids(str(h.scontrol)), {"10": "42"})


class Config(unittest.TestCase):
    def test_scontrol_is_optional_and_the_role_gives_the_slurm_installs(self):
        self.assertIn("scontrol", io_usage.CONFIG_OPTIONAL)
        self.assertNotIn("scontrol", io_usage.CONFIG_KEYS)
        vars_ = (ROOT / "roles/io_usage/vars/main.yml").read_text()
        self.assertIn('scontrol: "{{ slurm_install_prefix }}/bin/scontrol"', vars_)


class Logins(unittest.TestCase):
    def test_each_logins_disk_io_and_the_slices_waits(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            slice_ = h.cg / "user.slice" / f"user-{UID}.slice"
            slice_.mkdir(parents=True)
            (slice_ / "io.stat").write_text("8:16 rbytes=4096 wbytes=8192 rios=1 wios=2 dbytes=0 dios=0\n")
            (h.cg / "user.slice" / "io.pressure").write_text(
                "some avg10=0.00 avg60=0.00 avg300=0.00 total=2500000\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=1000000\n"
            )
            dev = h.sys / "dev/block/8:16"
            dev.mkdir(parents=True)
            (dev / "uevent").write_text("MAJOR=8\nMINOR=16\nDEVNAME=sdb\nDEVTYPE=disk\n")
            io_usage.run(h.cfg())
            text = h.text()
            self.assertIn(f'io_usage_login_read_bytes_total{{user="{NAME}",device="sdb"}} 4096', text)
            self.assertIn(f'io_usage_login_written_bytes_total{{user="{NAME}",device="sdb"}} 8192', text)
            self.assertIn('io_usage_slice_pressure_seconds_total{slice="user.slice",kind="some"} 2.5', text)
            self.assertNotIn('slice="system.slice"', text, "a slice without the file is left out")


if __name__ == "__main__":
    unittest.main()
