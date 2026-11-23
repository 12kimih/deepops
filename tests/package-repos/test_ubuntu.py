"""roles/package_repos tasks_from: ubuntu: the suites each source gets, with the
-security suite apart only when different mirrors serve it, and each source naming the
mirror list apt falls back along."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "lib"))
from ansible_yaml import condition, load, render, role_defaults  # noqa: E402

BLOCK = load("roles/package_repos/tasks/ubuntu.yml")[0]
TASKS = {t["name"]: t for t in BLOCK["block"]}


def values(**settings):
    v = {**role_defaults("package_repos"), **settings, "ansible_facts": {"distribution": "Ubuntu",
         "distribution_release": "noble"}}  # fmt: skip
    v.update({k: render(e, v) for k, e in BLOCK["vars"].items()})
    return v


def sources(**settings):
    v = values(**settings)
    archive = TASKS["Write the Ubuntu archive source"]["deb822_repository"]
    security = TASKS["Write the security source"]["deb822_repository"]
    return render(archive["suites"], v), render(security["state"], v), render(security["uris"], v)


class Ubuntu(unittest.TestCase):
    def test_one_mirror_serves_every_suite_in_one_source(self):
        suites, security, _ = sources(package_repos_ubuntu_mirrors=["https://m.example/ubuntu/"])
        self.assertEqual(suites, ["noble", "noble-updates", "noble-backports", "noble-security"])
        self.assertEqual(security, "absent")

    def test_security_from_another_mirror_is_its_own_source(self):
        suites, security, uris = sources(
            package_repos_ubuntu_mirrors=["https://m.example/ubuntu/"],
            package_repos_ubuntu_security_mirrors=["http://security.ubuntu.com/ubuntu/"],
        )
        self.assertNotIn("noble-security", suites)
        self.assertEqual((security, uris), ("present", ["mirror+file:/etc/apt/mirrors/ubuntu-security.list"]))

    def test_a_trailing_slash_is_the_same_mirror(self):
        _, security, _ = sources(
            package_repos_ubuntu_mirrors=["https://m.example/ubuntu/"],
            package_repos_ubuntu_security_mirrors=["https://m.example/ubuntu"],
        )
        self.assertEqual(security, "absent")

    def test_the_source_is_a_mirror_list_apt_falls_back_along(self):
        mirrors = ["https://a.example/ubuntu/", "http://archive.ubuntu.com/ubuntu/"]
        v = values(package_repos_ubuntu_mirrors=mirrors)
        archive = TASKS["Write the Ubuntu archive source"]["deb822_repository"]
        self.assertEqual(render(archive["uris"], v), ["mirror+file:/etc/apt/mirrors/ubuntu.list"])
        lists = TASKS["Write the mirror lists apt falls back along"]
        self.assertEqual(render(lists["loop"], v), ["ubuntu"])
        self.assertEqual(render(lists["copy"]["content"], {**v, "item": "ubuntu"}), "\n".join(mirrors) + "\n")

    def test_no_mirror_leaves_the_os_alone(self):
        self.assertFalse(condition(BLOCK["when"], values()))


if __name__ == "__main__":
    unittest.main()
