"""The GPU checks drain on the faults NVIDIA says need a reset or a reboot, only.

The old XID check read a fixed awk column of a log line whose layout changed with
the driver, looked back one hour (so a broken GPU was put back in service an hour
later by NHC itself), and drained on application faults such as XID 13 and 31.
"""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import role_defaults  # noqa: E402

CHECKS = ROOT / "roles" / "nhc" / "files" / "deepops.nhc"
DRAIN = " ".join(str(x) for x in role_defaults("nhc")["nhc_gpu_drain_xids"])

KERNEL_LOG = """\
NVRM: Xid (PCI:0000:3b:00): 13, pid=4242, name=python, Graphics Exception: ESR 0x504648=0x0
NVRM: Xid (PCI:0000:01:00 GPU-I:05): 94, pid=7194, Contained: CE User Channel (0x9). RST: No, D-RST: No
NVRM: Xid (PCI:0000:3b:00): 31, pid=4242, name=python, Ch 00000008, intr 00000000. MMU Fault
"""


def run(check, args="", kernel_log="", remapped=""):
    with tempfile.TemporaryDirectory() as tmp:
        for name, out in (("journalctl", kernel_log), ("nvidia-smi", remapped)):
            stub = Path(tmp, name)
            stub.write_text(f"#!/bin/sh\ncat <<'END'\n{out}END\n")
            stub.chmod(0o755)
        env = {**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}"}
        proc = subprocess.run(
            ["bash", "-c", f". {CHECKS}; {check} {args}"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    return proc.returncode, proc.stdout.strip()


class Xid(unittest.TestCase):
    def test_application_faults_keep_the_node(self):
        self.assertEqual(run("check_nv_xid", DRAIN, KERNEL_LOG), (0, ""))

    def test_a_fault_that_needs_a_reboot_drains(self):
        log = KERNEL_LOG + (
            "NVRM: Xid (PCI:0000:5a:00): 79, pid='<unknown>', name=<unknown>, GPU has fallen off the bus.\n"
            "NVRM: Xid (PCI:0000:01:00): 95, pid=7062, Uncontained: LTC TAG (0x2,0x0). RST: Yes, D-RST: No\n"
            "NVRM: Xid (PCI:0000:5a:00): 79, pid='<unknown>', name=<unknown>, GPU has fallen off the bus.\n"
        )
        rc, out = run("check_nv_xid", DRAIN, log)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "GPU fault, reboot to clear: XID 79 on PCI:0000:5a:00,XID 95 on PCI:0000:01:00")

    def test_a_number_inside_another_does_not_match(self):
        # 4 is not 46 and 140 is not 14.
        log = "NVRM: Xid (PCI:0000:01:00): 14, Channel 00000001\n"
        self.assertEqual(run("check_nv_xid", "4 140", log), (0, ""))

    def test_no_list_drains_nothing(self):
        log = "NVRM: Xid (PCI:0000:5a:00): 79, GPU has fallen off the bus.\n"
        self.assertEqual(run("check_nv_xid", "", log), (0, ""))


class RemappedRows(unittest.TestCase):
    def test_healthy_and_unsupported_pass(self):
        out = "00000000:3B:00.0, No, No\n00000000:5A:00.0, [N/A], [N/A]\n"
        self.assertEqual(run("check_nv_remapped_rows", remapped=out), (0, ""))

    def test_pending_or_failed_drains(self):
        out = "00000000:3B:00.0, Yes, No\n00000000:5A:00.0, 0, 1\n00000000:86:00.0, No, No\n"
        self.assertEqual(
            run("check_nv_remapped_rows", remapped=out),
            (1, "GPU row remapping: 00000000:3B:00.0 pending,00000000:5A:00.0 failed"),
        )


if __name__ == "__main__":
    unittest.main()
