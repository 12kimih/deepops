"""What root runs is root's alone.

slurmd runs the prolog, the epilog, their parts and the libraries they source as root.
Owned by the slurm account or group-writable for it, or in a directory it owns, any of
them is root for whoever holds that account: it can edit a file, or rename the
directory and put its own in its place. So nothing under the Slurm configuration
directory (slurm_config_dir, slurmctl_config_dir, /etc/slurm) is installed or created
as Slurm's. slurmdbd.conf is the exception Slurm itself demands (slurmdbd refuses one
it does not own), and it is a file root never runs.
"""

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import Scan, ansible_files, module, tasks  # noqa: E402

MODULES = {"file", "copy", "template", "lineinfile", "blockinfile", "ini_file"}
CONFIG_DIR = re.compile(r"slurm(ctl)?_config_dir|/etc/slurm\b")
SLURM = re.compile(r"^\{\{\s*slurm_(username|group)\s*\}\}$|^slurm$")
ALLOWED = {"slurmdbd.conf"}


def group_writable(mode):
    mode = str(mode or "")
    return bool(re.fullmatch(r"0?[0-7]?[0-7][2367][0-7]", mode)) or "g+w" in mode or "g=rw" in mode


def slurm_owned_config_paths(path):
    for task in tasks(path):
        name, args = module(task)
        targets = []
        if name in MODULES:
            targets.append((args.get("path") or args.get("dest") or "", args.get("owner"), args.get("group"),
                            args.get("mode")))  # fmt: skip
        if name in ("include_role", "import_role") and args.get("name") == "managed_dir":
            v = task.get("vars") or {}
            targets.append((v.get("managed_dir_path", ""), v.get("managed_dir_owner", "root"),
                            v.get("managed_dir_group", "root"), v.get("managed_dir_mode")))  # fmt: skip
        for target, owner, group, mode in targets:
            items = task.get("loop") or task.get("with_items") or []
            paths = [str(target)] + ([str(i) for i in items] if "item" in str(target) else [])
            if not any(CONFIG_DIR.search(p) for p in paths) or Path(str(target)).name in ALLOWED:
                continue
            owner = re.sub(r"\s+", " ", str(owner or ""))
            if SLURM.match(owner) or (SLURM.match(str(group or "")) and group_writable(mode)):
                yield task.get("name", "?")
            elif str(task.get("become_user", "")) in ("slurm", "{{ slurm_username }}"):
                yield task.get("name", "?")


class RootRunPathTests(unittest.TestCase):
    def test_nothing_under_the_slurm_config_dir_is_slurms(self):
        scan, found = Scan(minimum=200), {}
        for path in map(scan.seen, ansible_files()):
            names = list(slurm_owned_config_paths(path))
            if names:
                found[path.relative_to(ROOT).as_posix()] = names
        self.assertEqual(found, {}, "make these root's: slurmd runs what lives there as root")
        scan.check(self)

    def test_the_check_sees_each_form(self):
        with tempfile.TemporaryDirectory() as d:  # never in the shared working tree
            bad = Path(d) / "sample.yml"
            bad.write_text(
                "- name: parts\n  ansible.builtin.include_role: {name: managed_dir}\n"
                "  vars: {managed_dir_path: '{{ slurm_config_dir }}/prolog.d', managed_dir_owner: slurm}\n"
                "- name: dirs\n  ansible.builtin.file: {path: '{{ item }}', state: directory, owner: slurm}\n"
                "  loop: ['{{ slurm_config_dir }}', /var/log/slurm]\n"
                "- name: literal\n  file: path=/etc/slurm/shared owner={{slurm_username}} state=directory\n"
                "- name: group\n  copy: {dest: '{{ slurmctl_config_dir }}/x', group: slurm, mode: '0775'}\n"
                "- name: dbd\n  template: {dest: '{{ slurm_config_dir }}/slurmdbd.conf', owner: slurm}\n"
            )
            self.assertEqual(list(slurm_owned_config_paths(bad)), ["parts", "dirs", "literal", "group"])


if __name__ == "__main__":
    unittest.main()
