"""plugins/filter/apt.py, and that every apt pin a role writes goes through it."""

import importlib.util
import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import ansible_files, role_var_files, tracked  # noqa: E402

spec = importlib.util.spec_from_file_location("apt_filter", ROOT / "plugins" / "filter" / "apt.py")
apt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apt)


class PinPackages(unittest.TestCase):
    def test_every_pattern_matches_every_architecture(self):
        self.assertEqual(apt.apt_pin_packages("slurm-smd slurm-smd-*"), "slurm-smd:any slurm-smd-*:any")
        self.assertEqual(apt.apt_pin_packages(["*nvidia*"]), "*nvidia*:any")
        self.assertEqual(apt.apt_pin_packages("*"), "*:any")

    def test_a_named_architecture_is_kept(self):
        self.assertEqual(apt.apt_pin_packages("foo:i386 bar"), "foo:i386 bar:any")

    def test_a_regex_or_nothing_is_refused(self):
        for patterns in ("/nvidia/", "", []):
            with self.subTest(patterns=patterns), self.assertRaises(apt.AnsibleFilterError):
                apt.apt_pin_packages(patterns)


class Roles(unittest.TestCase):
    def test_every_pin_written_names_its_packages_through_the_filter(self):
        # A Package: line of an apt preferences file a task writes (not a debian/control
        # paragraph, which declares a package it builds).
        found = []
        for path in tracked("roles", "playbooks"):
            if path.suffix not in (".yml", ".yaml", ".j2") or "molecule" in path.parts:
                continue
            text = path.read_text(errors="ignore")
            for match in re.finditer(r"(?m)^\s*Package: (.+)$", text):
                after = text[match.end() : match.end() + 200]
                if "Pin:" in after and "apt_pin_packages" not in match.group(1):
                    found.append(f"{path.relative_to(ROOT)}: {match.group(0).strip()}")
        self.assertEqual(found, [], "write the Package line as {{ ... | apt_pin_packages }}")


# Installed at the version they have: apt moves them in the maintenance upgrade, with the
# kernel (roles/host_baseline kernel_packages).
HELD_ON_PURPOSE = {"host_baseline"}


def nodes(node):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from nodes(value)


# An apt name (Debian names are lower case) at a role's version: "name={{ x_version }}",
# or built as "'=' ~ x_version" (Docker's "\\1=5:' ~ ", with the epoch).
VERSIONED = re.compile(r"(?<![\w.+-])[a-z0-9][a-z0-9.+-]*=\{\{ \w*version|(?:'|\\\\1)=(?:5:)?' ~ ")


def version_pins(tree):
    """The pins of every repository definition (package_repos: uris and pin) in TREE."""
    for node in nodes(tree):
        if isinstance(node, dict) and "uris" in node and "pin" in node:
            pins = node["pin"]
            yield from [pins] if isinstance(pins, dict) else pins


class VersionsHeld(unittest.TestCase):
    def test_a_version_a_role_installs_by_name_is_held_through_upgrades(self):
        # An apt name=VERSION holds only that install: the maintenance role's dist upgrade
        # (and unattended upgrades) move the package on, and the role's next run fails as
        # a downgrade. The role holds the version with a pin (package_repos pin).
        roles = {}
        for path in ansible_files() + role_var_files():
            parts = path.relative_to(ROOT).parts
            if parts[0] != "roles":
                continue
            text = path.read_text()
            role = roles.setdefault(parts[1], {"versioned": False, "held": False})
            role["versioned"] |= bool(VERSIONED.search(text))
            role["held"] |= any(
                str(p.get("pin", "")).startswith("version ") for p in version_pins(yaml.safe_load(text))
            )
        unheld = sorted(
            r for r, v in roles.items() if v["versioned"] and not v["held"] and r not in HELD_ON_PURPOSE
        )
        self.assertEqual(unheld, [])
        held = {r for r, v in roles.items() if v["held"]}
        self.assertTrue({"docker", "nvidia_container_toolkit", "open_ondemand", "slurm"} <= held)


if __name__ == "__main__":
    unittest.main()
