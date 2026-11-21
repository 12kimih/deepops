"""The driver capabilities beyond compute (nvidia_driver_capabilities): what
roles/nvidia_driver installs and removes, and that they never read as a driver change."""

import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from ansible_yaml import ROOT, condition, load, render, role_defaults  # noqa: E402

ROLE = ROOT / "roles/nvidia_driver"
VARS = yaml.safe_load((ROLE / "vars/main.yml").read_text())
TASKS = load("roles/nvidia_driver/tasks/capabilities.yml")
ALL = ["compute", "utility", "graphics", "video"]


def values(distribution="Ubuntu", family="Debian", **settings):
    return {
        **role_defaults("nvidia_driver"),
        **VARS,
        "nvidia_driver_branch": "580",
        **settings,
        "ansible_facts": {"distribution": distribution, "os_family": family},
    }


def task(name):
    return next(t for t in TASKS if t["name"].startswith(name))


def installs(v):
    t = task("Install")
    return render(t["package"]["name"], v) if condition(t["when"], v) else []


def removes(v):
    t = task("Remove")
    return render(t["vars"]["host_baseline_absent_packages"], v) if condition(t["when"], v) else []


def accepted(capabilities):
    return condition(task("Check")["assert"]["that"], values(nvidia_driver_capabilities=capabilities))


class Capabilities(unittest.TestCase):
    def test_compute_only_by_default_and_nothing_else_is_left(self):
        v = values()
        self.assertEqual(v["nvidia_driver_capabilities"], ["compute", "utility"])
        self.assertEqual(installs(v), [])
        self.assertEqual(removes(v), ["libnvidia-gl-*", "libnvidia-decode-*", "libnvidia-encode-*"])

    def test_each_capability_is_the_archive_package_at_the_drivers_branch(self):
        got = installs(values(nvidia_driver_capabilities=ALL))
        for package in (
            "libnvidia-gl-580-server",
            "libnvidia-decode-580-server",
            "libnvidia-encode-580-server",
        ):
            self.assertIn(package, got)
        self.assertTrue({"libvulkan1", "libegl1", "vulkan-tools"} <= set(got), "graphics brings its loaders")
        self.assertEqual(removes(values(nvidia_driver_capabilities=ALL)), [])
        desktop = installs(values(nvidia_driver_capabilities=ALL, nvidia_driver_server_branch=False))
        self.assertIn("libnvidia-gl-580", desktop)

    def test_one_taken_out_is_removed_alone(self):
        v = values(nvidia_driver_capabilities=["compute", "utility", "graphics"])
        self.assertNotIn("libnvidia-encode-580-server", installs(v))
        self.assertEqual(removes(v), ["libnvidia-decode-*", "libnvidia-encode-*"])

    def test_nvidia_repos_carry_every_capability_so_only_loaders_are_added(self):
        for v in (values(nvidia_driver_capabilities=ALL, nvidia_driver_install_method="nvidia_repo"),
                  values("Rocky", "RedHat", nvidia_driver_capabilities=ALL)):  # fmt: skip
            got = installs(v)
            self.assertFalse([p for p in got if p.startswith("libnvidia-")], got)
            self.assertIn("vulkan-tools", got)
            self.assertEqual(removes(dict(v, nvidia_driver_capabilities=["compute", "utility"])), [])

    def test_only_the_offered_names_with_compute_and_utility(self):
        self.assertTrue(accepted(ALL))
        self.assertFalse(accepted(["compute", "utility", "display"]), "display needs an X server")
        self.assertFalse(accepted(["graphics"]), "compute and utility are the driver itself")

    def test_never_a_driver_change(self):
        # nvidia_driver_install decides the reboot (present.yml); a library is not one.
        for t in TASKS:
            self.assertNotEqual(t.get("register"), "nvidia_driver_install", t["name"])
        present = (ROLE / "tasks/present.yml").read_text()
        self.assertLess(present.index("redhat.yml"), present.index("capabilities.yml"),
                        "after the install, so a branch switch's purge is undone")  # fmt: skip


if __name__ == "__main__":
    unittest.main()
