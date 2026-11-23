"""The GPU checks drain on the faults NVIDIA says need a reset or a reboot, only.

The old XID check read a fixed awk column of a log line whose layout changed with
the driver, looked back one hour (so a broken GPU was put back in service an hour
later by NHC itself), and drained on application faults such as XID 13 and 31.
"""

from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import role_defaults  # noqa: E402
from nhc_checks import CHECKS, argv  # noqa: E402

DRAIN = " ".join(str(x) for x in role_defaults("nhc")["nhc_gpu_drain_xids"])
# NVIDIA's XID catalog (Xid-Catalog.xlsx of 2026-11-20, sheet XIDs): the codes in use
# whose "Tenant In-Operation Action" is RESET_GPU. A newer catalog that moves a code
# changes this snapshot and nhc_gpu_drain_xids together.
CATALOG_RESET_GPU = {
    46,
    62,
    64,
    95,
    110,
    119,
    120,
    140,
    143,
    155,
    156,
    158,
    160,
    166,
    167,
    169,
    175,
    179,
    180,
    181,
    184,
}
# Drains though the catalog says CONTACT_SUPPORT: the GPU has fallen off the bus.
DRAIN_ALSO = {79}

KERNEL_LOG = """\
NVRM: Xid (PCI:0000:3b:00): 13, pid=4242, name=python, Graphics Exception: ESR 0x504648=0x0
NVRM: Xid (PCI:0000:01:00 GPU-I:05): 94, pid=7194, Contained: CE User Channel (0x9). RST: No, D-RST: No
NVRM: Xid (PCI:0000:3b:00): 31, pid=4242, name=python, Ch 00000008, intr 00000000. MMU Fault
"""


def run(check, args="", kernel_log="", remapped="", tools=None):
    """(status, reason) of CHECK ARGS, with stub commands: journalctl and nvidia-smi
    print KERNEL_LOG and REMAPPED; TOOLS maps more names to (output, status), and a
    name mapped to None is missing."""
    stubs = {"journalctl": (kernel_log, 0), "nvidia-smi": (remapped, 0), **(tools or {})}
    with tempfile.TemporaryDirectory() as tmp:
        for name, stub in stubs.items():
            path = Path(tmp, name)
            if stub is None:
                path.write_text("#!/bin/sh\nexit 127\n")
            else:
                out, status = stub
                path.write_text(f"#!/bin/sh\ncat <<'END'\n{out}END\nexit {status}\n")
            path.chmod(0o755)
        env = {**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}"}
        proc = subprocess.run(
            argv(f"{check} {args}"),
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

    def test_the_list_is_the_catalogs_reset_class(self):
        self.assertEqual(set(role_defaults("nhc")["nhc_gpu_drain_xids"]), CATALOG_RESET_GPU | DRAIN_ALSO)

    def test_a_context_switch_timeout_alone_keeps_the_node(self):
        # The catalog: Xid 109 follows the action of an Xid 154 logged with it, else RESTART_APP.
        log = "NVRM: Xid (PCI:0000:3b:00): 109, pid=4242, name=python, Ch 00000010, errorString CTX SWITCH TIMEOUT\n"
        self.assertEqual(run("check_nv_xid", DRAIN, log), (0, ""))

    def test_xid_154_drains_on_its_latest_reset_or_reboot_verdict(self):
        bad = (
            "NVRM: Xid (PCI:0000:3b:00): 109, pid=4242, name=python, Ch 00000010\n"
            "NVRM: Xid (PCI:0000:3b:00): 154, GPU recovery action changed from 0x0 (None) to 0x2 (Node Reboot Required)\n"
            "NVRM: Xid (PCI:0000:5a:00): 154, GPU recovery action changed from 0x0 (None) to 0x3 (Drain P2P)\n"
        )
        self.assertEqual(
            run("check_nv_xid", DRAIN, bad),
            (1, "GPU fault, reboot to clear: XID 154 Node Reboot Required on PCI:0000:3b:00"),
        )
        for verdict in ("GPU Reset Required", "Drain and Reset"):
            with self.subTest(verdict=verdict):
                log = f"NVRM: Xid (PCI:0000:3b:00): 154, GPU recovery action changed from 0x0 (None) to 0x1 ({verdict})\n"
                self.assertEqual(run("check_nv_xid", DRAIN, log)[0], 1)
        cleared = (
            bad
            + "NVRM: Xid (PCI:0000:3b:00): 154, GPU recovery action changed from 0x2 (Node Reboot Required) to 0x0 (None)\n"
        )
        self.assertEqual(run("check_nv_xid", DRAIN, cleared), (0, ""), "the latest verdict counts")

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


class FailedTools(unittest.TestCase):
    """A check whose tool fails or reports what it cannot read fails, with its reason:
    passing on no output kept a node whose GPU hung nvidia-smi in service."""

    def test_nvidia_smi_failing_fails_every_gpu_check(self):
        broken = {"nvidia-smi": ("", 124)}
        for check, args in (
            ("check_nv_gpu_count", "8"),
            ("check_nv_remapped_rows", ""),
            ("check_nv_retired_pages", ""),
            ("check_nv_pcie_link_width", "16"),
        ):
            with self.subTest(check=check):
                self.assertEqual(
                    run(check, args, tools=broken),
                    (1, f"{check}: nvidia-smi{' -L' if check == 'check_nv_gpu_count' else ''} failed"),
                )

    def test_one_timed_out_nvidia_smi_is_tried_again(self):
        """A first try that times out (124) is made once more; only two in a row fail."""
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp, "nvidia-smi")
            stub.write_text(
                "#!/bin/sh\n"
                f"n=$(cat {tmp}/n 2>/dev/null || echo 0); echo $((n + 1)) > {tmp}/n\n"
                f'[ "$n" -lt "$(cat {tmp}/fails)" ] && exit 124\n'
                "printf 'GPU 0: NVIDIA GPU (UUID: GPU-0)\\nGPU 1: NVIDIA GPU (UUID: GPU-1)\\n'\n"
            )
            stub.chmod(0o755)
            env = {**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}"}
            for fails, expected in ((1, (0, "")), (2, (1, "check_nv_gpu_count: nvidia-smi -L failed"))):
                with self.subTest(fails=fails):
                    Path(tmp, "n").write_text("0")
                    Path(tmp, "fails").write_text(str(fails))
                    proc = subprocess.run(
                        argv("check_nv_gpu_count 2"), env=env, capture_output=True, text=True, check=False
                    )
                    self.assertEqual((proc.returncode, proc.stdout.strip()), expected)
                    self.assertEqual(Path(tmp, "n").read_text().strip(), str(min(fails + 1, 2)))

    def test_gpu_count(self):
        out = "GPU 0: A (UUID: a)\n  MIG 1g.10gb Device 0: (UUID: m)\nGPU 1: A (UUID: b)\n"
        self.assertEqual(run("check_nv_gpu_count", "2", tools={"nvidia-smi": (out, 0)}), (0, ""))
        self.assertEqual(
            run("check_nv_gpu_count", "8", tools={"nvidia-smi": (out, 0)}),
            (1, "check_nv_gpu_count: 2 GPUs, 8 expected"),
        )

    def test_link_width_not_read_fails(self):
        smi = {"nvidia-smi": ("00000000:3B:00.0, 16\n00000000:5A:00.0, [N/A]\n00000000:86:00.0, 8\n", 0)}
        self.assertEqual(
            run("check_nv_pcie_link_width", "16", tools=smi),
            (
                1,
                "check_nv_pcie_link_width: GPU PCIe link width not x16: 00000000:5A:00.0 x[N/A],00000000:86:00.0 x8",
            ),
        )
        ok = {"nvidia-smi": ("00000000:3B:00.0, 16\n", 0)}
        self.assertEqual(run("check_nv_pcie_link_width", "16", tools=ok), (0, ""))

    def test_retired_pages(self):
        smi = {"nvidia-smi": ("00000000:3B:00.0, No\n00000000:5A:00.0, [N/A]\n", 0)}
        self.assertEqual(run("check_nv_retired_pages", tools=smi), (0, ""))
        smi = {"nvidia-smi": ("00000000:3B:00.0, Yes\n", 0)}
        self.assertEqual(
            run("check_nv_retired_pages", tools=smi),
            (
                1,
                "check_nv_retired_pages: GPU retired pages pending on 00000000:3B:00.0, reboot to retire them",
            ),
        )

    def test_missing_dcgmi_fails(self):
        self.assertEqual(
            run("check_nv_dcgmi_diag", tools={"dcgmi": None}),
            (1, "check_nv_dcgmi_diag: DCGM diag -r 1 failed"),
        )
        self.assertEqual(
            run("check_nv_dcgmi_diag", tools={"dcgmi": ("|  Deployment  |  Pass  |\n", 0)}), (0, "")
        )

    def test_unreadable_kernel_log_fails(self):
        self.assertEqual(
            run("check_nv_xid", DRAIN, tools={"journalctl": ("", 1)}),
            (1, "check_nv_xid: the kernel log of this boot cannot be read"),
        )

    def test_ipoib_matches_the_netdev_exactly(self):
        out = "mlx5_0 port 1 ==> ib0 (Up)\nmlx5_1 port 1 ==> ib1 (Down)\n"
        tools = {"ibdev2netdev": (out, 0)}
        self.assertEqual(run("check_ipoib_up", "ib0", tools=tools), (0, ""))
        self.assertEqual(
            run("check_ipoib_up", "ib1", tools=tools), (1, "check_ipoib_up: IPoIB not up on ib1")
        )
        self.assertEqual(run("check_ipoib_up", "ib", tools=tools), (1, "check_ipoib_up: IPoIB not up on ib"))

    def test_ber_not_read_fails(self):
        good = {"mlxlink": ("Effective Physical BER          : 15E-255\n", 0)}
        bad = {"mlxlink": ("Effective Physical BER          : 1E-7\n", 0)}
        self.assertEqual(run("check_ib_link_ber", "mlx5_0", tools=good), (0, ""))
        self.assertEqual(
            run("check_ib_link_ber", "mlx5_0", tools=bad),
            (1, "check_ib_link_ber: BER on mlx5_0 is too high (1E-7)"),
        )
        self.assertEqual(
            run("check_ib_link_ber", "mlx5_0", tools={"mlxlink": ("", 1)}),
            (1, "check_ib_link_ber: no effective BER read on mlx5_0"),
        )

    def test_acs_reason_is_one_line_without_quotes(self):
        tools = {"acs": ("ACS on at 0000:01:00.0\nit's on at 0000:02:00.0\n", 1)}
        rc, out = run("check_pcie_acs", "acs", tools=tools)
        self.assertEqual((rc, out), (1, "ACS on at 0000:01:00.0, its on at 0000:02:00.0"))


class ExampleConf(unittest.TestCase):
    def test_every_check_the_example_names_exists(self):
        """The shipped example calls only checks NHC or deepops.nhc defines."""
        defined = set(re.findall(r"^(\w+)\(\)", CHECKS.read_text(), re.M))
        example = (ROOT / "roles" / "nhc" / "templates" / "nhc.conf.example-dgxa100").read_text()
        used = set(re.findall(r"\|\|\s*(check_(?:nv|ib|ipoib|num|pcie)\w*)", example))
        self.assertLessEqual(used, defined)


if __name__ == "__main__":
    unittest.main()
