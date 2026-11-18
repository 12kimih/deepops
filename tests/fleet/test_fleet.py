"""fleet_drift (plugins/filter/fleet.py)."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("fleet", ROOT / "plugins" / "filter" / "fleet.py")
fleet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fleet)


def state(kernel="6.8.0-149", packages=None, sysctl=None):
    return {"kernel": kernel, "packages": packages or {}, "sysctl": sysctl or {}}


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


if __name__ == "__main__":
    unittest.main()
