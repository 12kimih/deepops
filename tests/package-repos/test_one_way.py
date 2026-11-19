"""A package repository is added or removed one way: roles/package_repos, tasks_from: repo
(or its epel and cuda entries). A role writing its own source or key the way it likes
let the ways drift apart: keys trusted for every repository through apt-key, checks
without a fingerprint, .list files left behind, a deprecated module that goes away.
Roles not moved yet are listed with who moves them; the list only shrinks."""

import re
import sys
import unittest

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import Scan, ansible_files, module, tasks  # noqa: E402

REPO_MODULES = {"apt_repository", "deb822_repository", "yum_repository", "rpm_key", "apt_key"}
WRITERS = {"copy", "template", "file", "lineinfile", "blockinfile", "get_url"}
REPO_DIRS = (
    "/etc/apt/sources.list.d/",
    "/etc/apt/keyrings/",
    "/usr/share/keyrings/",
    "/etc/apt/trusted.gpg.d/",
    "/etc/yum.repos.d/",
    "/etc/pki/rpm-gpg/",
)
# A destination given through a variable shows only its name: one naming a source list,
# a keyring or a yum repo file counts too (docker_apt_sources, ..._keyring_path).
REPO_NAMES = re.compile(r"(?<![a-z])sources(?![a-z])|keyring|\.repo\b|_repo_file|gpg_key")
OWNER = "roles/package_repos/"
# file -> who moves it to roles/package_repos (tasks_from: repo)
NOT_MOVED_YET = {
    "roles/nvidia_container_toolkit/tasks/ubuntu.yml": "deepops or deepops-4",
    "roles/nvidia_container_toolkit/tasks/redhat.yml": "deepops or deepops-4",
}


def offences(path):
    for task in tasks(path):
        name, args = module(task)
        if name in REPO_MODULES:
            yield f"{task.get('name', '?')} ({name})"
        elif name in WRITERS:
            dest = str(args.get("dest") or args.get("path") or "")
            if dest.startswith(REPO_DIRS) or "sources.list.d" in dest or REPO_NAMES.search(dest):
                yield f"{task.get('name', '?')} ({name} {dest})"


class OneWayTests(unittest.TestCase):
    def test_repositories_go_through_package_repos(self):
        scan, found = Scan(NOT_MOVED_YET, minimum=200), {}
        for path in map(scan.seen, ansible_files()):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(OWNER):
                continue
            names = list(offences(path))
            if names and not scan.excuse(rel):
                found[rel] = names
        self.assertEqual(found, {}, "add or remove these through roles/package_repos, tasks_from: repo")
        scan.check(self)

    def test_the_check_sees_each_form(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            sample = Path(d) / "t.yml"
            sample.write_text(
                "- name: a\n  ansible.builtin.apt_repository: {repo: 'ppa:x/y'}\n"
                "- name: b\n  copy: {dest: /etc/apt/sources.list.d/x.list, content: 'deb x'}\n"
                "- name: c\n  ansible.builtin.get_url: {url: 'https://k', dest: /etc/apt/keyrings/k.asc}\n"
                "- name: d\n  copy: {dest: /etc/motd, content: hi}\n"
                "- name: e\n  copy: {dest: '{{ docker_apt_sources }}', content: 'Types: deb'}\n"
                "- name: f\n  template: {src: d.yml, dest: '{{ c }}/provisioning/datasources/d.yml'}\n"
            )
            self.assertEqual(
                [o.split(" (")[0] for o in offences(sample)], ["a", "b", "c", "e"], "every form, nothing else"
            )


if __name__ == "__main__":
    unittest.main()
