"""The packages roles/nvidia_driver installs from the Ubuntu archive: the full driver on
the precompiled modules by default, the compute-only set with nvidia_driver_headless."""

import fnmatch
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from ansible_yaml import ROOT, condition, load, render, role_defaults  # noqa: E402

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


class GraphicsDispatchers(unittest.TestCase):
    """glvnd's dispatchers and the Vulkan loader, by name wherever the driver renders: the
    driver packages do not depend on them, so a desktop's removal autoremoved them."""

    TASK = next(
        t
        for t in load("roles/nvidia_driver/tasks/present.yml")
        if t["name"].startswith("Install the graphics")
    )

    def installs(self, distribution="Ubuntu", family="Debian", **settings):
        values = {**role_defaults("nvidia_driver"), **VARS, "nvidia_driver_branch": "580", **settings,
                  "ansible_facts": {"os_family": family, "distribution": distribution}}  # fmt: skip
        if not condition(self.TASK["when"], values):
            return []
        return render(self.TASK["repo_package"]["name"], values)

    def test_the_full_driver_keeps_every_dispatcher_by_name(self):
        got = self.installs()
        for name in ("libegl1", "libgl1", "libglx0", "libopengl0", "libgles2", "libvulkan1", "libglvnd0"):
            self.assertIn(name, got)
        self.assertIn("vulkan-loader", self.installs("Rocky", "RedHat"))

    def test_the_headless_set_renders_nothing_and_takes_none(self):
        self.assertEqual(self.installs(nvidia_driver_headless=True), [])
        self.assertTrue(self.installs(nvidia_driver_headless=True, nvidia_driver_install_method="nvidia_repo"),
                        "NVIDIA's repository installs the full driver whatever headless says")  # fmt: skip

    def test_they_are_never_the_components_to_remove(self):
        owned = " ".join(VARS["_nvidia_driver_packages"]["Debian"])
        for name in VARS["_nvidia_driver_graphics_dispatchers"]["Debian"]:
            self.assertNotIn(name, owned)


class ArchivePin(unittest.TestCase):
    """The archive-only pin refuses the CUDA repository's NVIDIA packages but the ones that
    are not the driver's, exempt in a record ahead of it (apt takes the first match)."""

    def test_the_container_toolkit_is_exempt_and_nothing_of_the_drivers(self):
        exempt = VARS["nvidia_driver_archive_pin_exempt"]
        toolkit = role_defaults("nvidia_container_toolkit")["nvidia_container_toolkit_packages"]
        for name in toolkit:
            self.assertTrue(any(fnmatch.fnmatchcase(name, g) for g in exempt), name)
        for owned in VARS["_nvidia_driver_packages"]["Debian"]:
            example = owned.replace("*", "580-server")
            self.assertFalse(any(fnmatch.fnmatchcase(example, g) for g in exempt), owned)

    def test_the_exemption_comes_first(self):
        task = next(
            t for t in load("roles/nvidia_driver/tasks/ubuntu.yml") if t["name"].startswith("Take the NVIDIA")
        )
        content = next(v for k, v in task.items() if k.split(".")[-1] == "copy")["content"]
        self.assertLess(content.index("nvidia_driver_archive_pin_exempt"), content.index("'*nvidia*'"))
        self.assertIn("Pin-Priority: 500", content.split("'*nvidia*'")[0])


if __name__ == "__main__":
    unittest.main()
