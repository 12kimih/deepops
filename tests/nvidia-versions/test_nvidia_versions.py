"""plugins/filter/nvidia.py and the table in roles/nvidia_versions."""

import importlib.util
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("nvidia", ROOT / "plugins" / "filter" / "nvidia.py")
nv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nv)
TABLE = yaml.safe_load((ROOT / "roles/nvidia_versions/defaults/main.yml").read_text())[
    "nvidia_cuda_release_driver_branch"
]


class CudaDriver(unittest.TestCase):
    def test_each_major_runs_on_its_first_release_branch(self):
        self.assertEqual(nv.cuda_min_driver_branch(TABLE), {11: 450, 12: 525, 13: 580})

    def test_a_branch_ships_its_newest_release(self):
        self.assertEqual(nv.cuda_release_for_driver(TABLE, "580"), "13.0")
        self.assertEqual(nv.cuda_release_for_driver(TABLE, 595), "13.2")
        self.assertEqual(nv.cuda_release_for_driver(TABLE, "585"), "13.0")

    def test_ordering_is_numeric(self):
        self.assertEqual(nv.cuda_release_for_driver({"12.9": 575, "12.10": 576}, 576), "12.10")

    def test_a_branch_older_than_every_release_is_refused(self):
        with self.assertRaises(ValueError):
            nv.cuda_release_for_driver(TABLE, 400)


class KernelModule(unittest.TestCase):
    OPEN = "NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  580.95.05  Release Build  (dvs-builder@U22)  Tue Sep 23 2025\nGCC version:  gcc version 13.3.0"
    PROPRIETARY = "NVRM version: NVIDIA UNIX x86_64 Kernel Module  580.95.05  Tue Sep 23 09:00:00 UTC 2025\nGCC version:  gcc 13"

    def test_the_running_module_and_its_flavor(self):
        self.assertEqual(nv.nvidia_module_loaded(self.OPEN), "580.95.05 open")
        self.assertEqual(nv.nvidia_module_loaded(self.PROPRIETARY), "580.95.05 proprietary")
        self.assertEqual(nv.nvidia_module_loaded(""), "")
        self.assertEqual(nv.nvidia_module_loaded(None), "")

    def test_the_module_on_disk_and_its_flavor(self):
        self.assertEqual(nv.nvidia_module_on_disk("580.95.05\n", "Dual MIT/GPL"), "580.95.05 open")
        self.assertEqual(nv.nvidia_module_on_disk("580.95.05", "NVIDIA"), "580.95.05 proprietary")
        self.assertEqual(nv.nvidia_module_on_disk("", "NVIDIA"), "")

    def test_a_flavor_switch_at_one_version_is_not_current(self):
        self.assertNotEqual(
            nv.nvidia_module_loaded(self.PROPRIETARY), nv.nvidia_module_on_disk("580.95.05", "Dual MIT/GPL")
        )
        self.assertEqual(
            nv.nvidia_module_loaded(self.OPEN), nv.nvidia_module_on_disk("580.95.05", "Dual MIT/GPL")
        )


class DriverLeftovers(unittest.TestCase):
    # A compute-only (headless) install of the 580 server branch, open modules.
    HEADLESS = [
        "libnvidia-cfg1-580-server:amd64",
        "libnvidia-compute-580-server:amd64",
        "linux-modules-nvidia-580-server-open-6.8.0-149-generic",
        "linux-modules-nvidia-580-server-open-generic",
        "nvidia-compute-utils-580-server",
        "nvidia-firmware-580-server-580.178.04",
        "nvidia-headless-no-dkms-580-server-open",
        "nvidia-kernel-common-580-server",
        "nvidia-kernel-source-580-server-open",
        "nvidia-utils-580-server",
    ]

    def leftovers(self, installed, headless=False, modules="open", branch="580-server"):
        return nv.nvidia_driver_leftovers(installed, branch, modules, headless, "amd64")

    def test_the_same_branch_stays_and_only_the_other_userspace_goes_after(self):
        self.assertEqual(
            self.leftovers(self.HEADLESS),
            {"before": [], "after": ["nvidia-headless-no-dkms-580-server-open"]},
        )
        self.assertEqual(self.leftovers(self.HEADLESS, headless=True), {"before": [], "after": []})
        full = self.HEADLESS + ["nvidia-driver-580-server-open", "libnvidia-gl-580-server:amd64"]
        self.assertEqual(
            self.leftovers(full, headless=True), {"before": [], "after": ["nvidia-driver-580-server-open"]}
        )

    def test_another_architecture_goes_before(self):
        # A desktop driver's i386 libraries: libnvidia-egl-wayland1 is Multi-Arch: same,
        # so the i386 copy holds the amd64 one the full driver needs to its version.
        out = self.leftovers(
            self.HEADLESS + ["libnvidia-egl-wayland1:i386", "libnvidia-fbc1-595-server:i386"]
        )
        self.assertEqual(out["before"], ["libnvidia-egl-wayland1:i386", "libnvidia-fbc1-595-server:i386"])
        self.assertEqual(self.leftovers(["libnvidia-egl-wayland1:amd64"])["before"], [])

    def test_another_branch_goes_before(self):
        out = self.leftovers(
            self.HEADLESS + ["libnvidia-compute-570-server:amd64", "nvidia-driver-pinning-580"]
        )
        self.assertEqual(out["before"], ["libnvidia-compute-570-server:amd64", "nvidia-driver-pinning-580"])
        self.assertEqual(self.leftovers(self.HEADLESS, branch="580")["before"], self.HEADLESS)

    def test_the_other_flavour_goes_before(self):
        out = self.leftovers(self.HEADLESS, modules="proprietary")
        self.assertEqual(
            out["before"],
            [
                "linux-modules-nvidia-580-server-open-6.8.0-149-generic",
                "linux-modules-nvidia-580-server-open-generic",
                "nvidia-headless-no-dkms-580-server-open",
                "nvidia-kernel-source-580-server-open",
            ],
        )
        self.assertEqual(
            self.leftovers(["nvidia-kernel-source-580-server"])["before"], ["nvidia-kernel-source-580-server"]
        )


if __name__ == "__main__":
    unittest.main()
