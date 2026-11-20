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
CHANGELOG = """slurm-smd (26.05.6-1) UNRELEASED; urgency=medium

  * Initial release.
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

    def test_the_version_carries_the_packaging_revision(self):
        self.assertEqual(render(DEFAULTS["slurm_deb_version"], variables()), "26.05.6-1+deepops2")
        out = replace(CHANGELOG, "Version the packages {{ slurm_deb_version }}")
        self.assertTrue(out.startswith("slurm-smd (26.05.6-1+deepops2) UNRELEASED;"), out)

    def test_the_contribs_are_installed_and_packaged(self):
        """SchedMD's debian/ packages no contribs; seff is in the user guide."""
        values = variables()
        rules = render(
            find(BUILD, "Install the contribs debian/rules leaves out")["blockinfile"]["block"], values
        )
        self.assertTrue(rules.startswith("execute_after_dh_auto_install:\n"))
        for line in rules.splitlines()[1:]:
            self.assertTrue(line.startswith("\t"), line)  # a make recipe line
        for contrib in ("seff", "sgather", "sjobexit"):
            self.assertIn(f"/contribs/{contrib} install DESTDIR=$(CURDIR)/debian/tmp", rules)
        self.assertIn("install -D -m755 contribs/sjstat debian/tmp/usr/bin/sjstat", rules)
        files = find(BUILD, "Package the contribs")
        listed = {item["name"]: render(item["lines"], values) for item in files["loop"]}
        self.assertEqual(
            sorted(listed["slurm-smd-contribs.install"]),
            [f"usr/bin/{c}" for c in ("seff", "sgather", "sjobexitmod", "sjstat", "smail")],
        )
        self.assertIn("slurm-smd-contribs", DEFAULTS["slurm_packages_common"])

    def test_the_contribs_package_is_a_paragraph_of_its_own(self):
        block = render(find(BUILD, "Declare the contribs package")["blockinfile"]["block"], variables())
        control = CONTROL + "# BEGIN slurm-smd-contribs\n" + block + "\n# END slurm-smd-contribs\n"
        paragraphs = [p for p in re.split(r"\n\s*\n", control) if p.strip()]
        self.assertEqual(len(paragraphs), 2)
        self.assertRegex(paragraphs[1], r"(?m)^Package: slurm-smd-contribs$")
        self.assertIn(" slurm-smd-libslurm-perl (>= ${source:Version}),", paragraphs[1])

    def test_required_plugins_include_lua_nvml_metrics_pmix(self):
        for plugin in ("job_submit_lua.so", "gpu_nvml.so", "metrics_openmetrics.so", "mpi_pmix.so"):
            self.assertIn(plugin, DEFAULTS["slurm_deb_required_plugins"])


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

    def test_a_packaging_revision_is_no_version_change(self):
        note = find(VERSION, "Note the installed Slurm")
        values = variables(slurm_installed_deb={"rc": 0, "stdout": "ii 26.05.6-1"})
        values["_deb"] = render(note["vars"]["_deb"], values)
        self.assertEqual(render(note["vars"]["_deb_version"], values), "26.05.6")
        self.assertTrue(self.allowed("26.05.6", "26.05.6", upgrade=False))

    def test_a_change_needs_the_upgrade_playbook(self):
        self.assertFalse(self.allowed("25.11.6", "26.05.6", upgrade=False))
        self.assertTrue(self.allowed("25.11.6", "26.05.6", upgrade=True))
        self.assertTrue(self.allowed("26.05.6", "26.05.6", upgrade=False))
        self.assertTrue(self.allowed("", "26.05.6", upgrade=False))

    def test_the_source_build_needs_the_upgrade_playbook_even_at_the_same_version(self):
        self.assertFalse(self.allowed("26.05.6", "26.05.6", upgrade=False, source=True))
        self.assertTrue(self.allowed("26.05.6", "26.05.6", upgrade=True, source=True))


def walk(tasks):
    """Every task, inside block, rescue and always too."""
    for item in tasks:
        yield item
        for key in ("block", "rescue", "always"):
            yield from walk(item.get(key, []))


class TestCleanBuild(unittest.TestCase):
    """The packages build in a container, so nothing the build host has installed (a
    retired Slurm under /usr/local linked the Perl bindings on 2026-11-20) reaches them."""

    BUILD_COMMANDS = ("mk-build-deps", "dpkg-buildpackage", "debuild", "apt-get")

    def test_build_commands_run_in_the_container(self):
        for task in walk(BUILD):
            for module in ("command", "shell"):
                args = task.get(module)
                if args is None:
                    continue
                text = " ".join(args.get("argv", [])) if isinstance(args, dict) else str(args)
                text += " " + (args.get("cmd", "") if isinstance(args, dict) else "")
                for command in self.BUILD_COMMANDS:
                    if command == "apt-get" and "--simulate" in text:
                        continue  # the read-only look at the host's orphans
                    self.assertNotIn(command, text, f"{task.get('name')}: {command} runs on the host")

    def test_build_dependencies_are_not_installed_on_the_host(self):
        for task in walk(BUILD):
            apt = task.get("apt")
            if apt and apt.get("state", "present") == "present" and apt.get("name"):
                self.fail(f"{task.get('name')} installs {apt['name']} on the build host")

    def test_the_container_sees_only_the_trees_the_repository_and_the_cuda_source(self):
        task = find(BUILD, "Start the build container")
        targets = [m["target"] for m in task["docker_container"]["mounts"]]
        self.assertEqual(
            targets,
            [
                "{{ slurm_deb_build_dir }}",
                "{{ slurm_repo_dir }}",
                "{{ package_repos_cuda_keyring_file }}",
                "{{ package_repos_cuda_source_file }}",
            ],
        )
        self.assertTrue(all(m.get("read_only") for m in task["docker_container"]["mounts"][1:]))

    def test_the_container_is_removed_however_the_build_ends(self):
        block = find(BUILD, "Build in a clean container")
        removal = [
            t for t in block.get("always", []) if t.get("docker_container", {}).get("state") == "absent"
        ]
        self.assertEqual(len(removal), 1)

    def test_the_build_runs_as_the_build_account(self):
        clean = find(BUILD, "Build in a clean container")
        task = find(clean["block"], "Build the packages")["docker_container_exec"]
        self.assertIn("slurm_deb_build_account.uid", task["user"])
        self.assertEqual(task["argv"][0], "dpkg-buildpackage")


# From pyxis-0.24.0/debian (NVIDIA's packaging: the first changelog entry and the
# binary package's Depends, with an older entry the version edit must leave alone).
PYXIS_CHANGELOG = """nvslurm-plugin-pyxis (0.24.0-1) UNRELEASED; urgency=medium

  * Release v0.24.0

 -- NVIDIA CORPORATION <cudatools@nvidia.com>  Tue, 12 May 2026 04:06:41 +0000

nvslurm-plugin-pyxis (0.23.0-1) UNRELEASED; urgency=medium
"""
PYXIS_CONTROL = """Source: nvslurm-plugin-pyxis
Build-Depends: debhelper (>= 10), slurm-smd-dev | libslurm-dev

Package: nvslurm-plugin-pyxis
Architecture: any
Depends: ${shlibs:Depends}, ${misc:Depends}, enroot (>= 3.1.0) | enroot-hardened (>= 3.1.0)
"""


class PyxisPackage(unittest.TestCase):
    """pyxis is packaged with Slurm: versioned for it and depending on exactly it, so the
    plugin and the Slurm that loads it change in one apt transaction."""

    def deb_version(self):
        return render(render(DEFAULTS["slurm_pyxis_deb_version"], variables()), variables())

    def test_the_version_names_this_slurm(self):
        self.assertEqual(self.deb_version(), "0.24.0-1+slurm26.05.6.1+deepops2")

    def test_only_the_first_changelog_entry_is_renamed(self):
        values = variables()
        values["slurm_pyxis_deb_version"] = self.deb_version()
        task = find(BUILD, "Version pyxis for this Slurm")["replace"]
        out = re.sub(
            render(task["regexp"], values), render(task["replace"], values), PYXIS_CHANGELOG, flags=re.M
        )
        self.assertTrue(out.startswith("nvslurm-plugin-pyxis (0.24.0-1+slurm26.05.6.1+deepops2) UNRELEASED;"))
        self.assertIn("nvslurm-plugin-pyxis (0.23.0-1) UNRELEASED;", out)

    def test_it_depends_on_exactly_this_slurm_not_on_enroot(self):
        out = replace(PYXIS_CONTROL, "Make pyxis depend on this Slurm")
        self.assertIn("Depends: ${shlibs:Depends}, ${misc:Depends}, slurm-smd (= 26.05.6-1+deepops2)\n", out)
        self.assertNotIn("enroot", out)
        self.assertIn("Build-Depends: debhelper (>= 10), slurm-smd-dev | libslurm-dev", out)

    def test_it_is_installed_with_slurm(self):
        install = find(load("roles/slurm/tasks/packages.yml"), "Install the Slurm packages")
        self.assertIn("slurm_pyxis_package", install["apt"]["name"])

    def test_no_host_builds_pyxis(self):
        for path in sorted((ROOT / "roles" / "pyxis" / "tasks").glob("*.yml")):
            text = path.read_text()
            for word in ("make", "gcc", "build-essential"):
                self.assertIsNone(re.search(rf"\b{word}\b", text), f"{path.name} mentions {word}")


class PyxisEntry(unittest.TestCase):
    """The slurm role that installs pyxis also writes its `required` plugstack entry, in
    every run: an entry left by another play named a plugin path the upgrade deleted and
    stopped every slurmd (2026-11-20)."""

    PLUGSTACK = load("roles/slurm/tasks/plugstack.yml")

    def runs(self, name, wanted):
        task = find(self.PLUGSTACK, name)
        self.assertIsNotNone(task, name)
        return condition(task.get("when"), variables(slurm_pyxis_wanted=wanted))

    def test_the_entry_is_rendered_wherever_pyxis_is_wanted(self):
        self.assertTrue(self.runs("Configure pyxis", True))
        self.assertFalse(self.runs("Configure pyxis", False))
        self.assertIn("Restart slurmd", find(self.PLUGSTACK, "Configure pyxis")["notify"])

    def test_the_entry_names_the_path_the_package_is_checked_for(self):
        entry = (ROOT / "roles/slurm/templates/etc/slurm/plugstack.conf.d/pyxis.conf").read_text()
        self.assertTrue(entry.startswith("required {{ slurm_plugin_dir }}/spank_pyxis.so "))
        check = find(BUILD, "Check the pyxis package puts the plugin where Slurm loads it, for this Slurm")
        self.assertIn(
            "slurm_plugin_dir ~ '/spank_pyxis.so'", " ".join(check["assert"]["that"]).replace('"', "'")
        )

    def test_switched_off_the_entry_and_the_package_go(self):
        self.assertTrue(self.runs("Remove pyxis's plugstack entry", False))
        self.assertFalse(self.runs("Remove pyxis's plugstack entry", True))
        remove = find(load("roles/slurm/tasks/packages.yml"), "Remove pyxis")
        self.assertEqual(remove["include_role"], {"name": "host_baseline", "tasks_from": "absent"})
        self.assertFalse(condition(remove["when"], variables(slurm_pyxis_wanted=True)))
        self.assertTrue(condition(remove["when"], variables(slurm_pyxis_wanted=False)))

    def test_no_other_role_writes_the_entry(self):
        for role in sorted((ROOT / "roles").iterdir()):
            if role.name == "slurm":
                continue
            for path in sorted(role.glob("tasks/**/*.yml")) + sorted(role.glob("templates/**/*plugstack*")):
                self.assertNotIn("plugstack.conf", path.read_text(), str(path))


class NodeAccess(unittest.TestCase):
    """pam_slurm_adopt fatal()s the sshd process when it cannot initialise Slurm, which
    closed every SSH login to the nodes on 2026-11-20: the operators must never reach it,
    and the configuration it reads must not go before slurmd has its own copy."""

    COMPUTE = load("roles/slurm/tasks/compute.yml")

    def test_the_operators_list_comes_before_pam_slurm_adopt(self):
        blocks = [
            t["blockinfile"]["block"]
            for t in self.COMPUTE
            if t.get("blockinfile", {}).get("path") == "/etc/pam.d/sshd"
        ]
        self.assertEqual(len(blocks), 2)
        for block in blocks:
            lines = block.splitlines()
            self.assertIn("pam_listfile.so", lines[0])
            self.assertTrue(all("pam_slurm_adopt" not in line for line in lines[:1]))
            self.assertTrue(any("pam_slurm_adopt" in line for line in lines[1:]))

    def test_local_configuration_goes_only_after_slurmd_has_its_copy(self):
        names = [t.get("name") for t in self.COMPUTE]
        remove = names.index("Remove local copies of the configuration slurmctld serves")
        self.assertLess(names.index("Start slurmd"), remove)
        wait = names.index("Wait for slurmd's copy of the configuration")
        self.assertLess(wait, remove)
        self.assertEqual(self.COMPUTE[wait]["wait_for"]["path"], "/run/slurm/conf/slurm.conf")


if __name__ == "__main__":
    unittest.main()
