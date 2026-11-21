"""Packages built per kernel follow kernel upgrades and leave with their kernel.

linux-tools-$(uname -r), installed by name for cpupower, stayed marked manual after
its kernel was gone (one set per kernel ever booted) and a new kernel had no cpupower
until the next deploy. Roles now install them through host_baseline's
kernel_packages (the flavour metapackage, plus the running kernel's marked automatic),
and the flavour comes from one filter (plugins/filter/kernel.py).
"""

import importlib.util
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import ansible_files  # noqa: E402

spec = importlib.util.spec_from_file_location("kernel", ROOT / "plugins" / "filter" / "kernel.py")
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)


def pkgs(**versions):
    return {name.replace("_", "-"): [{"version": v}] for name, v in versions.items()}


class Flavour(unittest.TestCase):
    def test_generic_without_hwe(self):
        packages = pkgs(linux_image_generic="6.8.0-149.149", linux_image_6_8_0_149_generic="6.8.0-149.149")
        self.assertEqual(kernel.kernel_flavour(packages, "6.8.0-149-generic"), "generic")

    def test_the_newest_meta_boots(self):
        packages = {
            "linux-image-generic": [{"version": "6.8.0-149.149"}],
            "linux-image-generic-hwe-24.04": [{"version": "7.0.0-43.43~24.04.1"}],
        }
        self.assertEqual(kernel.kernel_flavour(packages, "6.8.0-149-generic"), "generic-hwe-24.04")

    def test_another_flavour_is_not_taken(self):
        packages = {
            "linux-image-generic": [{"version": "6.8.0-149.149"}],
            "linux-image-nvidia": [{"version": "6.8.0-1067.70"}],
            "linux-image-unsigned-6.8.0-1067-nvidia": [{"version": "6.8.0-1067.70"}],
        }
        self.assertEqual(kernel.kernel_flavour(packages, "6.8.0-1067-nvidia"), "nvidia")

    def test_no_meta_falls_back_to_the_running_flavour(self):
        self.assertEqual(kernel.kernel_flavour({}, "6.8.0-149-lowlatency"), "lowlatency")

    def test_a_non_ubuntu_release_is_refused(self):
        with self.assertRaises(ValueError):
            kernel.kernel_flavour({}, "5.14.0-427.el9.x86_64")


class OneWay(unittest.TestCase):
    def test_no_role_installs_a_running_kernel_package_by_name(self):
        """linux-<kind>-{{ ansible_facts.kernel }} goes through kernel_packages."""
        offenders = []
        for path in ansible_files():
            if "nvidia-dgx" in path.parts or path.name == "kernel_packages.yml":
                continue  # NVIDIA's DGX roles are kept as shipped
            text = path.read_text(errors="replace")
            if "linux-" in text and "-{{ ansible_facts.kernel }}" in text:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_the_task_file_parses(self):
        tasks = yaml.safe_load((ROOT / "roles/host_baseline/tasks/kernel_packages.yml").read_text())
        self.assertTrue(any("change_command" in t for t in tasks))


if __name__ == "__main__":
    unittest.main()
