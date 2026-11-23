"""cluster_tools.io_usage: what each job and login is counted, from a stand-in /proc and
cgroup tree."""

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
        for p in (self.proc, self.cg, self.sys):
            p.mkdir()

    def pid(self, pid, cgroup, rchar, wchar, uid=UID, ppid=1, start=100):
        p = self.proc / str(pid)
        p.mkdir(exist_ok=True)
        (p / "stat").write_text(f"{pid} (a b) S {ppid} " + " ".join(["0"] * 17) + f" {start} 0 0\n")
        (p / "cgroup").write_text(f"0::{cgroup}\n")
        (p / "io").write_text(f"rchar: {rchar}\nwchar: {wchar}\nsyscr: 1\n")
        (p / "status").write_text(f"Name:\tx\nPPid:\t{ppid}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")

    def gone(self, pid):
        for f in (self.proc / str(pid)).iterdir():
            f.unlink()
        (self.proc / str(pid)).rmdir()

    def cfg(self):
        return {"proc_root": str(self.proc), "cgroup_root": str(self.cg), "sys_root": str(self.sys),
                "slices": ["system.slice", "user.slice"], "textfile": str(self.d / "prom/io-usage.prom"),
                "state_file": str(self.d / "state/state.json")}  # fmt: skip

    def text(self):
        return (self.d / "prom/io-usage.prom").read_text()


STEP = "/system.slice/slurmstepd.scope/job_{}/step_0/user/task_0"


class Jobs(unittest.TestCase):
    def test_a_jobs_processes_are_summed_under_its_user_and_others_are_not_counted(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.pid(10, STEP.format(7), 100, 10)
            h.pid(11, STEP.format(7), 50, 5)
            h.pid(12, "/system.slice/slurmstepd.scope/job_7/step_0/slurm", 1, 1, uid=0)
            h.pid(13, "/user.slice/user-1000.slice/session-1.scope", 999, 999)
            h.pid(14, STEP.format(70), 3, 4)
            io_usage.run(h.cfg(), now=1000)
            text = h.text()
            self.assertIn(f'io_usage_job_read_bytes_total{{job_id="7",user="{NAME}"}} 151', text)
            self.assertIn(f'io_usage_job_written_bytes_total{{job_id="7",user="{NAME}"}} 16', text)
            self.assertIn(f'io_usage_job_read_bytes_total{{job_id="70",user="{NAME}"}} 3', text)
            self.assertNotIn("999", text)
            self.assertIn("io_usage_last_success_timestamp_seconds{} 1000", text)

    def test_the_counter_never_goes_back_when_a_process_takes_its_counts_away(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.pid(10, STEP.format(7), 100, 10)
            h.pid(11, STEP.format(7), 50, 5)
            io_usage.run(h.cfg())
            h.gone(11)  # a step ended, its counts went with slurmstepd
            h.pid(10, STEP.format(7), 120, 10)
            io_usage.run(h.cfg())
            self.assertIn('job_id="7"', h.text())
            self.assertIn(f'io_usage_job_read_bytes_total{{job_id="7",user="{NAME}"}} 170', h.text())
            h.pid(10, STEP.format(7), 130, 10)
            io_usage.run(h.cfg())
            self.assertIn(f'io_usage_job_read_bytes_total{{job_id="7",user="{NAME}"}} 180', h.text())

    def test_a_child_reaped_into_its_parent_is_not_counted_twice(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.pid(10, STEP.format(7), 100, 10)
            h.pid(11, STEP.format(7), 50, 5, ppid=10)
            io_usage.run(h.cfg())
            h.gone(11)
            h.pid(10, STEP.format(7), 100 + 60, 10 + 5)  # the child's last 60/5 now in its parent
            io_usage.run(h.cfg())
            self.assertIn(f'io_usage_job_read_bytes_total{{job_id="7",user="{NAME}"}} 160', h.text())
            self.assertIn(f'io_usage_job_written_bytes_total{{job_id="7",user="{NAME}"}} 15', h.text())

    def test_a_reused_pid_is_another_process(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.pid(10, STEP.format(7), 100, 10, start=100)
            h.pid(20, STEP.format(7), 1, 1)
            io_usage.run(h.cfg())
            h.pid(10, STEP.format(7), 5, 0, start=200)  # pid 10 exited (parent outside) and came back
            io_usage.run(h.cfg())
            self.assertIn(f'io_usage_job_read_bytes_total{{job_id="7",user="{NAME}"}} 106', h.text())

    def test_a_finished_job_leaves_the_file_and_the_state(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.pid(10, STEP.format(7), 100, 10)
            io_usage.run(h.cfg())
            h.gone(10)
            io_usage.run(h.cfg())
            self.assertNotIn('job_id="7"', h.text())
            self.assertEqual(json.loads(Path(h.cfg()["state_file"]).read_text()), {})

    def test_a_job_of_root_alone_is_roots(self):
        with tempfile.TemporaryDirectory() as d:
            h = Host(d)
            h.pid(10, STEP.format(8), 5, 5, uid=0)
            io_usage.run(h.cfg())
            self.assertIn('job_id="8",user="root"', h.text())


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
