"""What root runs is root's alone.

slurmd runs the prolog, the epilog, their parts and the libraries they source as root.
Owned by the slurm account, or in a directory it owns, any of them is root for whoever
holds that account: it can edit a file, or rename the directory and put its own in
its place. So nothing under slurm_config_dir is installed or created owned by Slurm.
slurmdbd.conf is the exception Slurm itself demands (slurmdbd refuses one it does not
own), and it is a file root never runs.
"""

from pathlib import Path
import unittest

import yaml

from test_managed_dirs import tasks

ROOT = Path(__file__).resolve().parents[3]
MODULES = (
    "ansible.builtin.file",
    "ansible.builtin.copy",
    "ansible.builtin.template",
    "file",
    "copy",
    "template",
)
SLURM_OWNERS = {"slurm", "{{ slurm_username }}"}
ALLOWED = ("slurmdbd.conf",)


def slurm_owned_config_paths(path):
    for task in tasks(yaml.safe_load(path.read_text()) or []):
        targets = []
        for module in MODULES:
            args = task.get(module)
            if isinstance(args, dict):
                targets.append((args.get("path") or args.get("dest") or "", args.get("owner"), task))
        include = task.get("ansible.builtin.include_role") or task.get("include_role")
        if isinstance(include, dict) and include.get("name") == "managed_dir":
            v = task.get("vars") or {}
            targets.append((v.get("managed_dir_path", ""), v.get("managed_dir_owner", "root"), task))
        for target, owner, t in targets:
            items = t.get("loop") or t.get("with_items") or []
            paths = [str(target)] + ([str(i) for i in items] if "item" in str(target) else [])
            if owner in SLURM_OWNERS and any("slurm_config_dir" in p for p in paths):
                if not str(target).endswith(ALLOWED):
                    yield t.get("name", "?")


class RootRunPathTests(unittest.TestCase):
    def test_nothing_under_the_slurm_config_dir_is_slurms(self):
        found = {}
        for path in sorted(ROOT.glob("roles/*/tasks/*.yml")):
            names = list(slurm_owned_config_paths(path))
            if names:
                found[str(path.relative_to(ROOT))] = names
        self.assertEqual(found, {}, "make these root's: slurmd runs what lives there as root")

    def test_the_check_sees_a_slurm_owned_prolog_dir(self):
        bad = ROOT / "scripts/validation/tests/.root-run-sample.yml"
        bad.write_text(
            "- name: parts\n  ansible.builtin.include_role: {name: managed_dir}\n"
            "  vars: {managed_dir_path: '{{ slurm_config_dir }}/prolog.d', managed_dir_owner: slurm}\n"
            "- name: dirs\n  ansible.builtin.file: {path: '{{ item }}', state: directory, owner: slurm}\n"
            "  loop: ['{{ slurm_config_dir }}', /var/log/slurm]\n"
        )
        try:
            self.assertEqual(list(slurm_owned_config_paths(bad)), ["parts", "dirs"])
        finally:
            bad.unlink()


if __name__ == "__main__":
    unittest.main()
