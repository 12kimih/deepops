#!/usr/bin/env python3
"""Inert package build and version regressions; run with Ansible's Python.

The build's edits to the shipped debian/ directory are applied to excerpts of the
26.05 control and rules files as Ansible's replace and blockinfile would apply them,
and the version checks are evaluated for upgrades Slurm does and does not support.
"""

import os
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from ansible_yaml import condition, load, render, role_defaults, same_name  # noqa: E402

ROOT = Path(os.environ.get("DEEPOPS_TEST_ROOT", Path(__file__).resolve().parents[2]))
DEFAULTS = role_defaults("slurm")
BUILD = load("roles/slurm/tasks/packages-build.yml")
VERSION = load("roles/slurm/tasks/version.yml")

# From slurm-26.05.6/debian (the lines the build edits, and their neighbours).
CONTROL = """Source: slurm-smd
Build-Depends:
 bash-completion,
 debhelper (>= 12),
 libhwloc-dev,
 liblua5.3-dev,
 libpmix-dev,
 python3,
Homepage: http://slurm.schedmd.com
"""
RULES = """override_dh_auto_configure:
\tdh_auto_configure -- --sysconfdir=/etc/slurm --disable-debug --with-pmix --enable-pam

override_dh_installsystemd:
\tdh_installsystemd -pslurm-smd-slurmctld --name=slurmctld
\tdh_installsystemd -pslurm-smd-slurmd --name=slurmd
"""


def find(tasks, name):
    for item in tasks:
        if same_name(item.get("name"), name):
            return item
        found = find(item.get("block", []), name) if "block" in item else None
        if found:
            return found
    return None


def variables(**overrides):
    return {**DEFAULTS, "ansible_facts": {"architecture": "x86_64", "processor_nproc": 4}, **overrides}


def replace(text, task_name, **overrides):
    """What ansible.builtin.replace does: re.sub of regexp with replace, multiline."""
    task = find(BUILD, task_name)
    args = task["replace"]
    values = variables(**overrides)
    values.update(find(BUILD, "Build the packages")["vars"])
    values.update({key: render(value, values) for key, value in task.get("vars", {}).items()})
    return re.sub(args["regexp"], render(args["replace"], values), text, flags=re.MULTILINE)


def paragraphs(text):
    """Deb822 paragraphs, split at empty lines as dpkg splits them."""
    return [block.splitlines() for block in re.split(r"\n\s*\n", text.strip()) if block.strip()]


def field(paragraph, name):
    """A field's entries: its value with the continuation lines, split at commas."""
    value, inside = [], False
    for line in paragraph:
        if line[:1] in (" ", "\t"):
            if inside:
                value.append(line)
        else:
            inside = line.startswith(f"{name}:")
            if inside:
                value.append(line.split(":", 1)[1])
    return [entry.strip() for entry in ",".join(value).split(",") if entry.strip()]


class PackageBuild(unittest.TestCase):
    def test_lua_54_replaces_the_shipped_53(self):
        control = replace(CONTROL, "Build against Lua {{ slurm_deb_lua_dev }}")
        self.assertIn(" liblua5.4-dev,\n", control)
        self.assertNotIn("liblua5.3", control)

    def test_extra_build_dependencies_and_nvml_package(self):
        control = replace(CONTROL, "Add the build dependencies debian/control lacks")
        # An empty line ends the paragraph: mk-build-deps then refuses the file
        # ("continued value line not in field"), as the first live build did.
        self.assertEqual(len(paragraphs(control)), 1, control)
        names = field(paragraphs(control)[0], "Build-Depends")
        self.assertEqual(names[0], "cuda-nvml-dev-13-0")
        for dep in DEFAULTS["slurm_deb_build_extra_deps"]:
            self.assertIn(dep, names)
        self.assertIn("libpmix-dev", names)  # the shipped ones stay

    def test_nvml_from_its_package_never_usr_local_cuda(self):
        rules = replace(RULES, "Configure with NVML from its package")
        self.assertIn(
            "dh_auto_configure -- --with-nvml=/usr/local/cuda-13.0/targets/x86_64-linux --sysconfdir", rules
        )
        rules = replace(
            RULES, "Configure with NVML from its package", ansible_facts={"architecture": "aarch64"}
        )
        self.assertIn("--with-nvml=/usr/local/cuda-13.0/targets/sbsa-linux", rules)

    def test_dpkg_starts_no_daemon(self):
        rules = replace(RULES, "Leave the daemons to the role")
        self.assertEqual(rules.count("dh_installsystemd --no-start --no-stop-on-upgrade -p"), 2)

    def test_the_driver_library_stays_out_of_the_dependencies(self):
        block = find(BUILD, "Keep the driver's libnvidia-ml out of the package dependencies")["blockinfile"][
            "block"
        ]
        self.assertEqual(block, "override_dh_shlibdeps:\n\tdh_shlibdeps -Xgpu_nvml.so")

    def test_required_plugins_include_lua_nvml_metrics_pmix(self):
        for plugin in ("job_submit_lua.so", "gpu_nvml.so", "metrics_openmetrics.so", "mpi_pmix.so"):
            self.assertIn(plugin, DEFAULTS["slurm_deb_required_plugins"])

    def test_the_build_runs_unprivileged(self):
        build = find(BUILD, "Build the packages")
        step = find(build["block"], "Build the packages")
        self.assertEqual(step["become_user"], "{{ slurm_deb_build_user }}")
        self.assertEqual(step["command"]["argv"][:2], ["dpkg-buildpackage", "-b"])


class VersionCheck(unittest.TestCase):
    SUPPORTED = find(VERSION, "Refuse a Slurm version change Slurm does not support")
    PLAYBOOK = find(VERSION, "Refuse a version change outside the upgrade playbook")

    def supported(self, installed, wanted):
        values = variables(slurm_installed_version=installed, slurm_version=wanted)
        values.update(self.SUPPORTED["vars"])
        return condition(self.SUPPORTED["assert"]["that"], values)

    def allowed(self, installed, wanted, upgrade, source=False):
        values = variables(
            slurm_installed_version=installed,
            slurm_version=wanted,
            slurm_upgrade=upgrade,
            slurm_source_build=source,
        )
        return condition(self.PLAYBOOK["assert"]["that"], values)

    def test_one_or_two_majors_up(self):
        self.assertTrue(self.supported("25.11.6", "26.05.6"))
        self.assertTrue(self.supported("25.05.3", "26.05.6"))
        self.assertTrue(self.supported("26.05.5", "26.05.6"))

    def test_three_majors_up_is_refused(self):
        self.assertFalse(self.supported("24.11.7", "26.05.6"))

    def test_down_is_refused(self):
        self.assertFalse(self.supported("26.05.6", "25.11.6"))
        self.assertFalse(self.supported("26.05.7", "26.05.6"))

    def test_a_change_needs_the_upgrade_playbook(self):
        self.assertFalse(self.allowed("25.11.6", "26.05.6", upgrade=False))
        self.assertTrue(self.allowed("25.11.6", "26.05.6", upgrade=True))
        self.assertTrue(self.allowed("26.05.6", "26.05.6", upgrade=False))
        self.assertTrue(self.allowed("", "26.05.6", upgrade=False))

    def test_the_source_build_needs_the_upgrade_playbook_even_at_the_same_version(self):
        self.assertFalse(self.allowed("26.05.6", "26.05.6", upgrade=False, source=True))
        self.assertTrue(self.allowed("26.05.6", "26.05.6", upgrade=True, source=True))


if __name__ == "__main__":
    unittest.main()
