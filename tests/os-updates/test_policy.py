"""The held-back list means the same thing to apt and to dnf.

os_updates_held_back is written once as shell globs. dnf-automatic reads the
globs as they are; unattended-upgrades needs regular expressions, which the
template derives. A package must be held back by both or by neither.
"""
from fnmatch import fnmatchcase
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import load, render  # noqa: E402

ROLE = ROOT / "roles" / "os_updates"
SAMPLES = [
    "linux-image-6.8.0-150-generic", "linux-firmware", "kernel-core", "nvidia-driver-620-open",
    "libnvidia-compute-620", "nvidia-container-toolkit", "cuda-toolkit-13-5", "libcudnn9-cuda-13",
    "libnccl2", "datacenter-gpu-manager-4-cuda13", "slurm-smd-slurmd", "munge", "libmunge2",
    "nfs-kernel-server", "nfs-common", "docker-ce", "containerd.io", "enroot",
    "openssl", "libc6", "openssh-server", "curl", "python3.12", "tzdata", "libssl3t64",
]


def apt_patterns(distribution="Ubuntu"):
    variables = {**load(ROLE / "defaults" / "main.yml"), **load(ROLE / "vars" / "main.yml"),
                 "ansible_facts": {"distribution": distribution}}
    text = render((ROLE / "templates" / "52deepops-unattended-upgrades.j2").read_text(), variables)
    block = text.split("Package-Blacklist {", 1)[1].split("};", 1)[0]
    return re.findall(r'"(.+)";', block), text


class HeldBackTests(unittest.TestCase):
    def test_apt_and_dnf_hold_back_the_same_packages(self):
        globs = load(ROLE / "defaults" / "main.yml")["os_updates_held_back"]
        patterns, _ = apt_patterns()
        self.assertEqual(len(patterns), len(globs))
        for name in SAMPLES:
            with self.subTest(package=name):
                by_dnf = any(fnmatchcase(name, g) for g in globs)
                by_apt = any(re.match(p, name) for p in patterns)
                self.assertEqual(by_apt, by_dnf)

    def test_security_fixes_still_flow(self):
        globs = load(ROLE / "defaults" / "main.yml")["os_updates_held_back"]
        for name in ("openssl", "libc6", "openssh-server", "libssl3t64"):
            self.assertFalse(any(fnmatchcase(name, g) for g in globs), name)

    def test_only_security_archives(self):
        for distribution in ("Ubuntu", "Debian"):
            _, text = apt_patterns(distribution)
            origins = text.split("Origins-Pattern {", 1)[1].split("};", 1)[0]
            with self.subTest(distribution=distribution):
                self.assertIn("security", origins)
                self.assertNotRegex(origins, r"-updates\b|-proposed|-backports")


if __name__ == "__main__":
    unittest.main()
