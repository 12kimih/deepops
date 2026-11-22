"""PCIe ACS goes off on every device that has it, and a device that keeps it fails.

The hand-installed unit wrote 0 to every PCI address and ignored every error, so a
device that kept ACS on (a firmware that locks the register) passed unseen, and
nothing noticed when a reset turned it back on. One program now turns it off at boot
and lists the devices where it is on, for the role and for NHC (check_pcie_acs).
"""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import load, render  # noqa: E402
from nhc_checks import argv as nhc_argv  # noqa: E402

PROGRAM = render((ROOT / "roles/pcie_acs/templates/pcie-acs.sh.j2").read_text(), {"ansible_managed": ""})

# setpci over a directory of register files: a device without a file has no ACS
# capability (setpci fails), one named *.stuck ignores writes.
SETPCI = """#!/bin/bash
dev=$2 reg=$3
file="$STATE/$dev"
[ -e "$file.stuck" ] && file="$file.stuck"
[ -e "$file" ] || { echo "setpci: capability not found" >&2; exit 1; }
case $reg in
*=*) [[ $file == *.stuck ]] || echo "${reg#*=}" > "$file" ;;
*) cat "$file" ;;
esac
"""


def run(mode, devices, nhc=False):
    """Run the program (or NHC's check of it) over DEVICES: {address: register or
    (register, 'stuck') or None for no capability}; returns rc, output, registers."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for sub in ("sys", "state", "bin"):
            (tmp / sub).mkdir()
        for dev, reg in devices.items():
            (tmp / "sys" / dev).mkdir()
            if reg is None:
                continue
            stuck = isinstance(reg, tuple)
            (tmp / "state" / (dev + (".stuck" if stuck else ""))).write_text(
                (reg[0] if stuck else reg) + "\n"
            )
        (tmp / "bin/setpci").write_text(SETPCI)
        (tmp / "bin/setpci").chmod(0o755)
        program = tmp / "pcie-acs"
        program.write_text(PROGRAM)
        program.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}",
            "STATE": str(tmp / "state"),
            "PCIE_ACS_DEVICES": str(tmp / "sys"),
        }
        argv = nhc_argv(f"check_pcie_acs {program}") if nhc else [str(program), mode]
        proc = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
        regs = {p.name: p.read_text().strip() for p in (tmp / "state").iterdir()}
    return proc.returncode, proc.stdout.strip(), regs


class Program(unittest.TestCase):
    def test_disable_turns_off_only_the_devices_with_acs_on(self):
        rc, out, regs = run("disable", {"0000:00:01.0": "001d", "0000:00:02.0": "0000", "0000:3b:00.0": None})
        self.assertEqual(rc, 0, out)
        self.assertEqual(regs, {"0000:00:01.0": "0000", "0000:00:02.0": "0000"})
        self.assertEqual(out, "0000:00:01.0: ACS control 001d -> 0000")

    def test_a_device_that_keeps_acs_on_fails(self):
        rc, out, _ = run("disable", {"0000:00:01.0": ("001d", "stuck"), "0000:00:02.0": "0011"})
        self.assertEqual(rc, 1)
        self.assertIn("PCIe ACS on: 0000:00:01.0 (001d)", out)
        self.assertNotIn("0000:00:02.0 (", out)

    def test_check_lists_and_changes_nothing(self):
        rc, out, regs = run("check", {"0000:00:01.0": "001d", "0000:00:02.0": "0000"})
        self.assertEqual((rc, out), (1, "PCIe ACS on: 0000:00:01.0 (001d)"))
        self.assertEqual(regs["0000:00:01.0"], "001d")
        self.assertEqual(run("check", {"0000:00:02.0": "0000", "0000:3b:00.0": None})[:2], (0, ""))

    def test_a_mode_is_required(self):
        self.assertEqual(run("", {})[0], 2)


class Nhc(unittest.TestCase):
    def test_acs_back_on_drains_with_the_devices_named(self):
        rc, out, _ = run(None, {"0000:00:01.0": "001d"}, nhc=True)
        self.assertEqual((rc, out), (1, "PCIe ACS on: 0000:00:01.0 (001d)"))
        self.assertEqual(run(None, {"0000:00:01.0": "0000"}, nhc=True)[:2], (0, ""))


class Iommu(unittest.TestCase):
    """pcie_acs_iommu picks the argument for the CPU's vendor (the nodes had it typed into
    /etc/default/grub by hand)."""

    def args(self, setting, vendor):
        variables = {
            **load(ROOT / "roles/pcie_acs/vars/main.yml"),
            "pcie_acs_iommu": setting,
            "ansible_facts": {"processor": ["0", vendor, "Some CPU"]},
        }
        return render("{{ pcie_acs_iommu_args }}", variables)

    def test_off_by_vendor_pt_and_nothing(self):
        self.assertEqual(self.args("off", "GenuineIntel"), ["intel_iommu=off"])
        self.assertEqual(self.args("off", "AuthenticAMD"), ["amd_iommu=off"])
        self.assertEqual(self.args("pt", "GenuineIntel"), ["iommu=pt"])
        self.assertEqual(self.args("", "GenuineIntel"), [])


if __name__ == "__main__":
    unittest.main()
