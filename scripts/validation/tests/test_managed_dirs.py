"""A set of files installed into a directory goes through roles/managed_dir.

A copy or template looped over a glob (with_fileglob, a fileglob or filetree lookup),
or a copy of a whole directory (src ending in "/"), only adds: a file dropped from the
sources stays on the host, and in a prolog.d, a rule directory or a Python package it
keeps running. managed_dir installs the set and removes everything else. A directory
others also write to records what it installed with roles/recorded_files instead: a
file that includes that role (as a task, not a word in a comment) is fine. A directory
the role does not own alone, and deliberately leaves alone, is listed with the reason.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import Scan, ansible_files, module, tasks  # noqa: E402

INSTALLERS = {"copy", "template", "synchronize"}
GLOBS = ("fileglob", "filetree")
# file -> why a glob install there must not remove what it did not install
SHARED = {
    # A new home is filled from /etc/skel once, as useradd does; it is its user's then.
    "roles/people/tasks/main.yml": "a new home's first files, the user's from then on",
}


def uses_recorded_files(path):
    return any(
        module(t)[0] in ("include_role", "import_role") and module(t)[1].get("name") == "recorded_files"
        for t in tasks(path)
    )


def globbed_installs(path):
    for task in tasks(path):
        name, args = module(task)
        if name not in INSTALLERS:
            continue
        loop = " ".join(str(task.get(k, "")) for k in task if k == "loop" or k.startswith("with_"))
        if any(k.startswith("with_file") for k in task) or any(g in loop for g in GLOBS):
            yield task.get("name", "?")
        elif str(args.get("src", "")).endswith("/"):
            yield task.get("name", "?")


class ManagedDirTests(unittest.TestCase):
    def test_globbed_installs_go_through_managed_dir(self):
        scan, found = Scan(SHARED, minimum=200), {}
        for path in map(scan.seen, ansible_files()):
            rel = path.relative_to(ROOT).as_posix()
            names = list(globbed_installs(path))
            if names and not scan.excuse(rel) and not uses_recorded_files(path):
                found[rel] = names
        self.assertEqual(found, {}, "install these through roles/managed_dir (see its tasks/main.yml)")
        scan.check(self)

    def test_the_check_sees_each_form(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            sample = Path(d) / "t.yml"
            sample.write_text(
                "- name: a\n  copy: src={{ item }} dest=/x/\n  with_fileglob: ['*']\n"
                "- name: b\n  ansible.legacy.template: {src: '{{ item.src }}', dest: /y}\n"
                "  loop: \"{{ query('filetree', 't') }}\"\n"
                "- name: c\n  copy: {src: files/, dest: /z/}\n"
                "# recorded_files is only a word here\n"
            )
            self.assertEqual(list(globbed_installs(sample)), ["a", "b", "c"])
            self.assertFalse(uses_recorded_files(sample))


if __name__ == "__main__":
    unittest.main()
