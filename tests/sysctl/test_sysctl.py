"""roles/host_baseline/tasks/sysctl.yml: the boot-order reading, and that each role's
kernel settings go to its own file, which boot applies after everyone else's."""

import importlib.util
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import render  # noqa: E402
from repo_scan import ansible_files  # noqa: E402

spec = importlib.util.spec_from_file_location("sysctl", ROOT / "plugins" / "filter" / "sysctl.py")
sysctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sysctl)

KEY = "kernel.apparmor_restrict_unprivileged_userns"
# What a node printed with the old file names: the role's file, then the administrator's
# /etc/sysctl.conf, which won.
BOOT = f"""# /usr/lib/sysctl.d/10-apparmor.conf
{KEY} = 1

# /etc/sysctl.d/90-enroot.conf
user.max_user_namespaces=65536
{KEY}=1

# /etc/sysctl.d/99-sysctl.conf
# /etc/sysctl.conf - Configuration file for setting system variables
kernel/apparmor_restrict_unprivileged_userns=0
"""


class BootOrder(unittest.TestCase):
    def test_the_last_file_wins(self):
        self.assertEqual(
            sysctl.sysctl_boot_values(BOOT)[KEY], {"value": "0", "file": "/etc/sysctl.d/99-sysctl.conf"}
        )

    def test_a_later_file_undoing_the_role_is_reported(self):
        self.assertEqual(
            sysctl.sysctl_boot_mismatches(BOOT, {KEY: "1", "user.max_user_namespaces": 65536}),
            [f"{KEY}: boot applies 0 from /etc/sysctl.d/99-sysctl.conf, not 1"],
        )

    def test_a_missing_key_is_reported(self):
        self.assertEqual(
            sysctl.sysctl_boot_mismatches("", {"vm.x": "1 2"}), ["vm.x: boot applies nothing, not 1 2"]
        )


def role_sysctl_calls():
    """(role, the vars of its include of host_baseline tasks_from: sysctl) for every call."""
    for path in ansible_files():
        parts = path.relative_to(ROOT).parts
        if parts[0] != "roles" or "molecule" in parts:
            continue
        for task in yaml.safe_load(path.read_text()) or []:
            include = task.get("ansible.builtin.include_role") or {}
            if include.get("name") == "host_baseline" and include.get("tasks_from") == "sysctl":
                yield parts[1], task["vars"]


def role_vars(role):
    out = {}
    for name in ("defaults", "vars"):
        path = ROOT / "roles" / role / name / "main.yml"
        if path.is_file():
            out.update(yaml.safe_load(path.read_text()) or {})
    return out


class RoleFiles(unittest.TestCase):
    def test_each_role_writes_its_own_file_named_after_it(self):
        prefix = yaml.safe_load((ROOT / "roles/deepops_paths/defaults/main.yml").read_text())[
            "deepops_sysctl_prefix"
        ]
        calls = list(role_sysctl_calls())
        self.assertEqual(sorted({r for r, _ in calls}), ["coredump", "docker", "enroot", "nfs_common"])
        for role, args in calls:
            with self.subTest(role=role):
                path = render(
                    args["host_baseline_sysctl_file"], role_vars(role) | {"deepops_sysctl_prefix": prefix}
                )
                self.assertEqual(path, f"{prefix}{role}.conf")

    def test_the_role_files_sort_after_the_administrators(self):
        prefix = yaml.safe_load((ROOT / "roles/deepops_paths/defaults/main.yml").read_text())[
            "deepops_sysctl_prefix"
        ]
        name = Path(prefix + "a.conf").name
        self.assertEqual(sorted([name, "99-sysctl.conf", "99-zz.conf", "README.conf"])[-1], name)

    def test_no_key_is_set_by_two_roles(self):
        # The role files sort among themselves by name, so a key two roles set would be
        # decided by the names, not by anyone.
        owner = {}
        for role, args in role_sysctl_calls():
            values = args["host_baseline_sysctls"]
            if isinstance(values, str):
                values = role_vars(role)[values.strip("{} ")]
            for key in values:
                with self.subTest(key=key):
                    self.assertIn(owner.setdefault(key, role), (role,), f"{key}: {owner[key]} and {role}")


if __name__ == "__main__":
    unittest.main()
