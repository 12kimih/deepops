"""Every container image a role runs is pinned by digest, so a moved or rewritten tag can
never change what a service runs (roles/container_image enforces it at deploy time; this
catches it at commit time)."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PINNED = re.compile(r"@sha256:[0-9a-f]{64}$")
IMAGE_VAR = re.compile(r'^([a-z0-9_]+_(?:container|image)): "([^"]+)"', re.M)
# Images built on the host from a pinned Dockerfile, and non-image values with the suffix.
LOCAL = {"slurm_exporter_container", "firmware_update_container"}


class PinnedImagesTest(unittest.TestCase):
    def test_role_defaults(self):
        found = 0
        for defaults in sorted(ROOT.glob("roles/*/defaults/main.yml")):
            if defaults.parts[-3] == "galaxy":
                continue
            text = defaults.read_text()
            scalars = dict(re.findall(r'^([a-z0-9_]+): "([^"]*)"', text, re.M))
            for name, value in IMAGE_VAR.findall(text):
                if name in LOCAL:
                    continue
                found += 1
                # Resolve references to other defaults of the same role (version, digest).
                rendered = re.sub(
                    r"\{\{\s*([a-z0-9_]+)\s*\}\}", lambda m: scalars.get(m.group(1), "?"), value
                )
                self.assertRegex(
                    rendered, PINNED, f"{defaults.relative_to(ROOT)}: {name} is not pinned by digest"
                )
        self.assertGreater(found, 5)

    def test_dockerfiles(self):
        for dockerfile in ROOT.glob("roles/*/files/**/Dockerfile"):
            text = dockerfile.read_text()
            for ref in re.findall(r"^ARG [A-Z_]*IMAGE=(\S+)", text, re.M):
                self.assertRegex(ref, PINNED, f"{dockerfile.relative_to(ROOT)}: {ref}")
            for ref in re.findall(r"^FROM (\S+)", text, re.M):
                if not ref.startswith("${"):
                    self.assertRegex(ref, PINNED, f"{dockerfile.relative_to(ROOT)}: FROM {ref}")


if __name__ == "__main__":
    unittest.main()
