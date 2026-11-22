"""fleet_drift (plugins/filter/fleet.py)."""

import importlib.util
import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import ansible_files  # noqa: E402

spec = importlib.util.spec_from_file_location("fleet", ROOT / "plugins" / "filter" / "fleet.py")
fleet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fleet)


def state(kernel="6.8.0-149", packages=None, sysctl=None, loaded="", on_disk="", reboot="", machine_id=""):
    return {
        "kernel": kernel,
        "packages": packages or {},
        "sysctl": sysctl or {},
        "nvidia_loaded": loaded,
        "nvidia_on_disk": on_disk,
        "reboot_pending": reboot,
        "machine_id": machine_id,
    }


class FleetDrift(unittest.TestCase):
    def test_identical_hosts_have_no_drift(self):
        s = state(packages={"libc6": "2.39-0ubuntu8.6"}, sysctl={"kernel.x": "1"})
        self.assertEqual(fleet.fleet_drift({"a": s, "b": dict(s)}), [])

    def test_kernel_and_sysctl_differences_name_the_hosts(self):
        drift = fleet.fleet_drift(
            {
                "a": state(sysctl={"kernel.apparmor_restrict_unprivileged_userns": "1"}),
                "b": state(sysctl={"kernel.apparmor_restrict_unprivileged_userns": "1"}),
                "c": state("6.8.0-124", sysctl={"kernel.apparmor_restrict_unprivileged_userns": "0"}),
            }
        )
        self.assertIn("kernel: 6.8.0-149 on a,b; 6.8.0-124 on c", drift)
        self.assertIn("sysctl kernel.apparmor_restrict_unprivileged_userns: 1 on a,b; 0 on c", drift)

    def test_a_package_on_one_host_only_is_not_drift(self):
        drift = fleet.fleet_drift(
            {"a": state(packages={"mariadb-server": "1"}), "b": state(packages={"curl": "8"}), "c": state()}
        )
        self.assertEqual(drift, [])

    def test_a_shared_package_at_two_versions_is(self):
        drift = fleet.fleet_drift(
            {"a": state(packages={"curl": "8.5"}), "b": state(packages={"curl": "8.4"})}
        )
        self.assertEqual(drift, ["package curl: 8.4 on b; 8.5 on a"])

    def test_a_driver_loaded_at_another_version_than_installed_is(self):
        drift = fleet.fleet_drift(
            {
                "a": state(loaded="580.95.05", on_disk="580.95.05"),
                "b": state(loaded="580.82.07", on_disk="580.95.05"),
                "ctl": state(),  # no GPU: no module, not drift
            }
        )
        self.assertEqual(
            drift,
            [
                "nvidia module loaded: 580.82.07 on b; 580.95.05 on a",
                "nvidia module on b: 580.82.07 loaded, 580.95.05 installed",
            ],
        )

    def test_a_module_installed_but_not_loaded_is_not_a_version_mismatch(self):
        self.assertEqual(fleet.fleet_drift({"a": state(on_disk="580.95.05"), "b": state()}), [])

    def test_a_pending_reboot_is_drift_even_on_level_packages(self):
        drift = fleet.fleet_drift({"a": state(reboot="linux-image-6.8.0-150-generic"), "b": state()})
        self.assertEqual(drift, ["reboot pending on a: linux-image-6.8.0-150-generic"])

    def test_hosts_sharing_a_machine_id_are_named(self):
        drift = fleet.fleet_drift(
            {"a": state(machine_id="1f"), "b": state(machine_id="1f"), "c": state(machine_id="2e")}
        )
        self.assertEqual(len(drift), 1)
        self.assertTrue(drift[0].startswith("machine-id shared by a,b:"), drift)


class SysctlOwners(unittest.TestCase):
    def test_each_compared_sysctl_names_the_playbook_whose_role_sets_it(self):
        # The comparison's failure tells the operator which playbook brings a differing
        # sysctl back; a name that went stale would send them to the wrong run.
        owners = yaml.safe_load((ROOT / "roles/maintenance/defaults/main.yml").read_text())[
            "maintenance_consistency_sysctls"
        ]
        self.assertIsInstance(owners, dict)
        for key, playbook in owners.items():
            with self.subTest(key=key):
                text = (ROOT / playbook).read_text()
                roles = set(re.findall(r"(?:role|name): ([a-z_]+)", text))
                setters = [
                    p
                    for p in ansible_files()
                    if p.parent.name == "tasks" and re.search(rf"^\s+{re.escape(key)}:", p.read_text(), re.M)
                ]
                self.assertTrue(setters, f"no role sets {key}")
                self.assertTrue({p.parts[-3] for p in setters} & roles, f"{playbook} runs none of {setters}")

    def test_each_package_installed_from_a_release_file_names_its_playbook(self):
        # apt-upgrade.yml cannot level a package no repository carries; its role's
        # playbook can, and the comparison's failure must say which.
        defaults = yaml.safe_load((ROOT / "roles/maintenance/defaults/main.yml").read_text())
        owners = defaults["maintenance_consistency_packages"]
        from_files = {
            p.relative_to(ROOT).parts[1]
            for p in ansible_files()
            if p.parent.name == "tasks" and re.search(r"^\s+deb: ", p.read_text(), re.M)
        }
        named = set()
        for playbook in set(owners.values()):
            text = (ROOT / playbook).read_text()
            named |= set(re.findall(r"(?:role|name): ([a-z_-]+)", text))
        # The CUDA keyring's .deb bootstraps the CUDA repository, which then carries it.
        levelled_by_their_repository = {"package_repos"}
        self.assertTrue(from_files, "no role installs a release file")
        self.assertEqual(
            from_files - named - levelled_by_their_repository,
            set(),
            "add the package and its playbook to maintenance_consistency_packages",
        )


if __name__ == "__main__":
    unittest.main()
