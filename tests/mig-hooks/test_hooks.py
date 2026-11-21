#!/usr/bin/env python3
"""Render the actual MIG apply command without running GPU operations.

Run using the project's Ansible Python environment:
  python tests/mig-hooks/test_hooks.py
"""

import os
from pathlib import Path
import unittest

import yaml

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from ansible_yaml import canonical, render, same_name  # noqa: E402
from repo_scan import module  # noqa: E402

ROOT = Path(os.environ.get("DEEPOPS_TEST_ROOT", Path(__file__).resolve().parents[2]))
PLAY = canonical(yaml.safe_load((ROOT / "playbooks/nvidia-software/nvidia-mig.yml").read_text()))[0]
APPLY = next(task for task in PLAY["tasks"] if same_name(task.get("name"), "Apply MIG configuration"))
DEFAULTS = yaml.safe_load((ROOT / "roles/deploy_defaults/defaults/main.yml").read_text())


class Hooks(unittest.TestCase):
    """The apply runs as the package's service.sh does: configuration, hooks and profile in
    the environment, so the package's apply-start hook persists them for the boot-time
    reapply (mig-parted deployments/systemd)."""

    def render(self, values):
        variables = {**DEFAULTS, "inventory_dir": "/cfg", **values}
        return [render(arg, variables) for arg in module(APPLY)[1]["argv"]]

    def test_the_packaged_hooks_and_the_selected_profile_reach_the_environment(self):
        args = self.render({"mig_manager_profile": "all-1g.10gb"})
        self.assertEqual(args[:5], ["systemd-run", "--wait", "--pipe", "--collect", "--quiet"])
        self.assertIn("--setenv=MIG_PARTED_HOOKS_FILE=/etc/nvidia-mig-manager/hooks.yaml", args)
        self.assertIn("--setenv=MIG_PARTED_CONFIG_FILE=/etc/nvidia-mig-manager/config.yml", args)
        self.assertIn("--setenv=MIG_PARTED_SELECTED_CONFIG=all-1g.10gb", args)
        self.assertEqual(args[args.index("--") + 1 :], ["nvidia-mig-parted", "apply"])

    def test_a_site_hooks_file_with_spaces_stays_one_argument(self):
        args = self.render({"mig_manager_hooks": "/etc/mig/custom hooks.yaml"})
        self.assertIn("--setenv=MIG_PARTED_HOOKS_FILE=/etc/mig/custom hooks.yaml", args)


if __name__ == "__main__":
    unittest.main()
