"""Keep version pins quoted in the docs equal to the role defaults.

A doc that restates `name: version` for a role variable goes stale the next
time the role is bumped. Docs should show a placeholder and point at the role;
where they do quote a value, it must be the current default (or the value the
example config sets).
"""

from pathlib import Path
import re
import sys
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import image_pins  # noqa: E402
from repo_files import tracked  # noqa: E402

PIN = re.compile(
    r"\b([a-z][a-z0-9_]*(?:version|_container|_image|_tag)[a-z0-9_]*)\s*[:=]\s*[\"']?([vV]?\d[\w.\-:/]*)"
)


# A component named in prose, and the role default that pins it.
PROSE_PINS = {
    "Slurm": "slurm_version",
    "Enroot": "enroot_version",
    "enroot": "enroot_version",
    "Pyxis": "slurm_pyxis_version",
    "pyxis": "slurm_pyxis_version",
    "NHC": "nhc_version",
    "Lmod": "lmod_version",
    "OpenMPI": "openmpi_version",
    "Open MPI": "openmpi_version",
    "EasyBuild": "easybuild_version",
    "Container Toolkit": "nvidia_container_toolkit_version",
    "Docker Engine": "docker_engine_version",
    "MIG Manager": "mig_manager_version",
}
PROSE = re.compile(r"\b(" + "|".join(map(re.escape, PROSE_PINS)) + r")\s+v?(\d+\.\d+(?:\.\d+)?)")
# Words beside a version that make it a requirement or history, not the pin.
PROSE_NOT_A_PIN = ("or newer", "or later", "since", "before", "older", "earlier")
# Pages that record a past test environment as such.
PROSE_HISTORY = {"docs/k8s-cluster/roce-perf-k8s.md"}


def role_defaults():
    found = {}
    for path in tracked("roles/*/defaults/main.yml"):
        for name, value in (yaml.safe_load(path.read_text()) or {}).items():
            if isinstance(value, (str, int, float)) and "{{" not in str(value):
                found.setdefault(name, str(value))
    return found


def example_values():
    found = {}
    for path in tracked("config.example/group_vars/*.yml"):
        for line in path.read_text().splitlines():
            m = re.match(r"^([a-z][a-z0-9_]+):\s*[\"']?([^\"'#\s]+)", line)
            if m:
                found[m.group(1)] = m.group(2)
    return found


# A Python package an example pins (examples/*/requirements.txt), by the names docs and
# URLs give it.
PACKAGE_NAMES = {"vllm": ("vLLM", "vllm"), "huggingface_hub": ("huggingface_hub", "huggingface-hub", "Hub")}


def example_pins():
    """{package: version} from the examples' requirements files."""
    found = {}
    for path in tracked("examples/*/requirements*.txt"):
        for line in path.read_text().splitlines():
            m = re.match(r"^([A-Za-z0-9_.-]+)==([^\s#;]+)", line)
            if m:
                found[m.group(1).lower().replace("-", "_")] = m.group(2)
    return found


class DocPinTests(unittest.TestCase):
    def test_quoted_pins_match_defaults(self):
        defaults, examples = role_defaults(), example_values()
        docs = tracked("docs/*.md", "roles/*/README.md", "README.md")
        for doc in docs:
            for number, line in enumerate(doc.read_text().splitlines(), 1):
                for name, value in PIN.findall(line):
                    value = value.rstrip(".,)`\"'")
                    if name in defaults and value not in (defaults[name], examples.get(name)):
                        with self.subTest(doc=str(doc.relative_to(ROOT)), line=number):
                            self.fail(f"{name}={value} but the role default is {defaults[name]}")

    def test_docs_name_pinned_images_at_their_tags(self):
        """A page that names an image the roles pin (prom/prometheus:v3.x) goes stale at
        the next bump: it must name the pinned tag, or better, no tag at all (the
        air-gap skill reads the list from image_pins.py). Dockerfile build stages are
        not deployed images, and generic bases such as ubuntu appear in other senses."""
        pinned = {}
        for ref, _, where in image_pins.pins():
            if "Dockerfile" in where:
                continue
            _, path, tag = image_pins.split(ref)
            pinned.setdefault(path.removeprefix("library/"), set()).add(tag)
        docs = tracked("docs/*.md", "roles/*/README.md", "skills/*.md", "README.md", "AGENTS.md")
        stale = []
        for doc in docs:
            for number, line in enumerate(doc.read_text().splitlines(), 1):
                for repo, tags in pinned.items():
                    # A tag starts with a word character: "nvidia/cuda:..." is prose.
                    for m in re.finditer(rf"(?<![\w.-]){re.escape(repo)}:(\w[\w.-]*)", line):
                        if m.group(1) not in tags:
                            stale.append(f"{doc.relative_to(ROOT)}:{number}: {repo}:{m.group(1)}")
        self.assertEqual(stale, [], "name the pinned tag, or none (docs/deepops/versions.md lists them)")

    def test_prose_names_the_pinned_version(self):
        """ "Slurm 25.11.x" in a sentence went stale with the next bump as surely as a
        `slurm_version:` line: a component named in prose with a version names the one
        its role pins (that version or its major.minor). A version that only states a
        requirement or history says so ("or newer", "since") and is not a pin."""
        defaults = role_defaults()
        stale = []
        for doc in tracked("docs/*.md", "roles/*/README.md", "skills/*.md", "README.md", "AGENTS.md"):
            rel = str(doc.relative_to(ROOT))
            if rel in PROSE_HISTORY:
                continue
            for number, line in enumerate(doc.read_text().splitlines(), 1):
                for m in PROSE.finditer(line):
                    name, version = m.group(1), m.group(2)
                    pinned = defaults[PROSE_PINS[name]]
                    around = line[max(0, m.start() - 12) : m.end() + 12].lower()
                    if pinned.startswith(version) or any(w in around for w in PROSE_NOT_A_PIN):
                        continue
                    stale.append(f"{rel}:{number}: {name} {version}, the role pins {pinned}")
        self.assertEqual(stale, [], "name the variable or docs/deepops/versions.md, not a number")

    def test_docs_name_the_examples_pinned_packages(self):
        """vllm-quickstart.md and shared-cache.md quoted vLLM and huggingface_hub at
        their pins in prose and in every tagged link: a bump of the requirements file
        left them at the old release. A version beside a requirement word (>=, or
        newer) or the page's own history is not a pin."""
        pins = example_pins()
        self.assertLessEqual(set(PACKAGE_NAMES), set(pins), "a name list for a package no example pins")
        stale = []
        for doc in tracked("docs/*.md", "skills/*.md", "README.md", "AGENTS.md", "examples/*.md"):
            for number, line in enumerate(doc.read_text().splitlines(), 1):
                for package, names in PACKAGE_NAMES.items():
                    alias = "|".join(map(re.escape, names))
                    for m in re.finditer(
                        rf"(?<![\w-])(?:{alias})(?![\w-])[^\s)\]]{{0,30}}?(?:==|[/@ ]v?|\s+\*\*v?)(\d+\.\d+\.\d+)",
                        line,
                    ):
                        around = line[m.start() : m.end() + 12].lower()
                        if m.group(1) == pins[package] or any(w in around for w in (*PROSE_NOT_A_PIN, ">=")):
                            continue
                        stale.append(
                            f"{doc.relative_to(ROOT)}:{number}: {m.group(0)}, the example pins {pins[package]}"
                        )
        self.assertEqual(stale, [], "quote the version examples/*/requirements.txt pins")


if __name__ == "__main__":
    unittest.main()
