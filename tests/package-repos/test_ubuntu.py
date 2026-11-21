"""roles/package_repos tasks_from: ubuntu: the suites each source gets, with the
-security suite apart only when a different mirror serves it."""

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
        suites, security, _ = sources(package_repos_ubuntu_mirror="https://m.example/ubuntu/")
        self.assertEqual(suites, ["noble", "noble-updates", "noble-backports", "noble-security"])
        self.assertEqual(security, "absent")

    def test_security_from_another_mirror_is_its_own_source(self):
        suites, security, uris = sources(
            package_repos_ubuntu_mirror="https://m.example/ubuntu/",
            package_repos_ubuntu_security_mirror="http://security.ubuntu.com/ubuntu/",
        )
        self.assertNotIn("noble-security", suites)
        self.assertEqual((security, uris), ("present", ["http://security.ubuntu.com/ubuntu/"]))

    def test_a_trailing_slash_is_the_same_mirror(self):
        _, security, _ = sources(
            package_repos_ubuntu_mirror="https://m.example/ubuntu/",
            package_repos_ubuntu_security_mirror="https://m.example/ubuntu",
        )
        self.assertEqual(security, "absent")

    def test_no_mirror_leaves_the_os_alone(self):
        self.assertFalse(condition(BLOCK["when"], values()))


if __name__ == "__main__":
    unittest.main()
