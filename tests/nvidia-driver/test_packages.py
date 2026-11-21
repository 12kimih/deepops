"""The packages roles/nvidia_driver installs from the Ubuntu archive: the full driver on
the precompiled modules by default, the compute-only set with nvidia_driver_headless."""

import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from ansible_yaml import ROOT, load, render, role_defaults  # noqa: E402

ROLE = ROOT / "roles/nvidia_driver"
VARS = yaml.safe_load((ROLE / "vars/main.yml").read_text())
TASK = next(
    t for t in load("roles/nvidia_driver/tasks/ubuntu.yml") if t["name"].startswith("Determine the NVIDIA")
)


def packages(**settings):
    values = {
        **role_defaults("nvidia_driver"),
        **VARS,
        "nvidia_driver_branch": "580",
        **settings,
        "ansible_facts": {
            "os_family": "Debian",
            "distribution": "Ubuntu",
            "kernel": "6.8.0-149-generic",
            "packages": {"linux-generic": [{"version": "6.8.0.149.149"}]},
        },  # fmt: skip
    }
    # kernel_flavour (plugins/filter/kernel.py, its own tests) gives the flavour the host boots.
    values["_kernel_flavour"] = "generic"
    for name in ("_branch", "_flavoured"):
        values[name] = render(TASK["vars"][name], values)
    return render(TASK["set_fact"]["nvidia_driver_packages_resolved"], values)


class ArchivePackages(unittest.TestCase):
    def test_the_full_driver_on_precompiled_modules_by_default(self):
        # The modules package provides nvidia-dkms-580-server-open: nothing compiles.
        self.assertEqual(
            packages(), ["linux-modules-nvidia-580-server-open-generic", "nvidia-driver-580-server-open"]
        )

    def test_headless_keeps_compute_and_the_tools_only(self):
        self.assertEqual(
            packages(nvidia_driver_headless=True),
            [
                "linux-modules-nvidia-580-server-open-generic",
                "nvidia-headless-no-dkms-580-server-open",
                "nvidia-utils-580-server",
            ],  # fmt: skip
        )

    def test_the_flavours_follow_the_toggles(self):
        self.assertEqual(
            packages(nvidia_driver_kernel_modules="proprietary", nvidia_driver_server_branch=False),
            ["linux-modules-nvidia-580-generic", "nvidia-driver-580"],
        )

    def test_an_override_is_taken_as_written(self):
        self.assertEqual(packages(nvidia_driver_package="nvidia-driver-570-open"), ["nvidia-driver-570-open"])


if __name__ == "__main__":
    unittest.main()
