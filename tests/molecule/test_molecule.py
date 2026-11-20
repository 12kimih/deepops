"""The molecule scenarios share one platform and one CI list.

.config/molecule/config.yml holds what every scenario shares (platform, driver, the
repository's Ansible paths), and scripts/deepops/molecule.py is the one list of roles
with a scenario, which CI runs. Before, 38 scenario files each restated the platform
(two Ubuntu releases, never pinned) and the CI matrix was a hand list that had left 12
roles' scenarios untested."""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

BASE = ROOT / ".config" / "molecule" / "config.yml"
SHARED = {"dependency", "driver", "platforms", "verifier"}


def load(path):
    """PATH as YAML, molecule's ${VAR} placeholders taken as text."""
    return yaml.safe_load(re.sub(r"\$\{[^}]*\}", "x", path.read_text())) or {}


class Molecule(unittest.TestCase):
    def test_the_base_platform_is_pinned(self):
        (platform,) = load(BASE)["platforms"]
        self.assertRegex(platform["image"], r"@sha256:[0-9a-f]{64}$")
        self.assertIn("DEEPOPS_MOLECULE_INSTANCE", BASE.read_text(), "a role's containers carry its name")

    def test_the_platform_cannot_reach_the_host(self):
        """A privileged container, or one given host devices, lets a role configure the
        Docker host's own hardware (the bmc scenario set the host BMC's address), and its
        writable /proc/sys lets the image's systemd-sysctl set the host kernel's keys."""
        (platform,) = load(BASE)["platforms"]
        self.assertFalse(platform.get("privileged", False))
        self.assertFalse([o for o in platform.get("security_opts", []) if "systempaths" in o])
        self.assertFalse(platform.get("devices"))
        self.assertLessEqual(set(platform.get("capabilities", [])), {"SYS_ADMIN", "SYS_PTRACE"})
        self.assertNotIn("network_mode", platform)

    def test_scenarios_state_only_what_they_add(self):
        scenarios = tracked("roles/*/molecule/*/molecule.yml")
        self.assertGreater(len(scenarios), 20)
        for path in scenarios:
            data = load(path)
            where = path.relative_to(ROOT)
            with self.subTest(scenario=str(where)):
                self.assertFalse(SHARED & set(data), f"{where} restates {sorted(SHARED & set(data))}")
                provisioner = data.get("provisioner", {})
                self.assertFalse({"name", "ansible_args", "env"} & set(provisioner), f"{where}: provisioner")

    def test_the_platform_is_a_node_for_service_starts(self):
        """The image's policy-rc.d refuses every start a package asks for; a node has
        none. The platform removes it once: a scenario's own copy (six once wrote
        `exit 0`) hides what roles/quiet_install does about a foreign one."""
        (platform,) = load(BASE)["platforms"]
        self.assertIn("rm -f /usr/sbin/policy-rc.d", platform["command"])
        self.assertIn("exec /lib/systemd/systemd", platform["command"])
        for path in tracked("roles/*/molecule/*/*.yml"):
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertNotIn("policy-rc.d", path.read_text())

    def test_ci_runs_every_role_with_a_scenario(self):
        listed = json.loads(
            subprocess.run(
                [sys.executable, str(ROOT / "scripts/deepops/molecule.py"), "list"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        on_disk = sorted({p.parts[-4] for p in tracked("roles/*/molecule/*/molecule.yml")})
        self.assertEqual(listed, on_disk)
        workflow = yaml.safe_load((ROOT / ".github/workflows/molecule.yml").read_text())
        matrix = workflow["jobs"]["build"]["strategy"]["matrix"]["deepops-role"]
        self.assertIsInstance(matrix, str, "the matrix comes from molecule.py list, not a hand list")
        self.assertIn("needs.roles.outputs.roles", matrix)
        steps = " ".join(s.get("run", "") for s in workflow["jobs"]["roles"]["steps"])
        self.assertIn("scripts/deepops/molecule.py list", steps)


if __name__ == "__main__":
    unittest.main()
