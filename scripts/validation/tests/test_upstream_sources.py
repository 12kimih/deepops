"""Every version deepops pins names where its releases are published.

scripts/deepops/upstream.py reads the `# upstream:` line above each pin to report the
pins behind their upstream; a pin without one would silently drop out of that report
and age. Container images (repo:tag@sha256) and pyproject.toml's pins need no line.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

# A version pin: a *_version or *_branch setting whose value is a literal version
# (minimums such as runai_gpu_operator_min_version are requirements, not pins).
PIN = re.compile(r'^([a-z_][a-z0-9_]*(?:_version|_branch)):\s*"?(v?\d+(?:\.\d+)*(?:-[\w.~]+)?)"?\s*$')
SOURCE = re.compile(r"^\s*#\s*upstream:\s*(git|pypi|helm|deb|oci|cuda|manual|hold)\b")


class UpstreamSources(unittest.TestCase):
    def test_every_pin_names_its_upstream(self):
        missing, pins = [], 0
        for path in tracked("roles/*/defaults/main.yml", "roles/*/vars/*.yml", "roles/*/vars/*.yaml"):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith("roles/galaxy/"):
                continue
            lines = path.read_text().splitlines()
            for number, line in enumerate(lines, 1):
                m = PIN.match(line)
                if not m or m.group(1).endswith("_min_version"):
                    continue
                pins += 1
                if not (number > 1 and SOURCE.match(lines[number - 2])):
                    missing.append(f"{rel}:{number}: {m.group(1)}")
        self.assertGreater(pins, 20)
        self.assertEqual(
            missing, [], "add `# upstream: <kind> <args>` above it (see scripts/deepops/upstream.py)"
        )

    def test_every_annotation_is_one_upstream_py_reads(self):
        """A `registry` line narrows the image pin below it and is read by the image pass;
        every other line is a version source upstream.py resolves (or lists, held)."""
        sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
        import upstream

        kinds = {kind for _, _, kind, _, _ in upstream.annotated_pins()}
        self.assertNotIn("registry", kinds)
        self.assertLessEqual(kinds, {"git", "pypi", "helm", "deb", "oci", "cuda", "manual", "hold"})
        narrowed = [where for _, _, where in upstream.image_pins.pins() if upstream.annotation_above(where)]
        self.assertTrue(any("deploy_defaults" in w for w in narrowed), "the CUDA check image is narrowed")


class RequiresPython(unittest.TestCase):
    """`pypi NAME PY` reports the newest release that runs on PY: setuptools 83 needs
    Python 3.10, so a plain newest-release check asked for a bump that breaks RHEL 9."""

    def test_admits(self):
        sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
        import upstream

        for spec, python, expected in (
            (None, "3.9", True),
            (">=3.9", "3.9", True),
            (">=3.10", "3.9", False),
            (">=3.8, !=3.9.*", "3.9", False),
            (">=3.8, !=3.9.*", "3.12", True),
            ("==3.*", "3.9", True),
            ("<3.9", "3.9", False),
            ("~=3.9", "3.12", True),
            ("~=3.9.0", "3.10", False),
        ):
            with self.subTest(spec=spec, python=python):
                self.assertIs(upstream.admits(spec, python), expected)


if __name__ == "__main__":
    unittest.main()
