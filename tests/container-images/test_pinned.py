"""Every container image deepops runs is pinned by digest in one place, a role's
defaults or vars, so a moved or rewritten tag can never change what runs, and
scripts/deepops/upstream.py sees every pin (roles/container_image enforces the digest at
deploy time; this catches it at commit time).

The values are read as YAML, not matched as text: an unquoted pin is a pin too (the
NGC check images once escaped a quoted-only pattern). Playbooks, tasks, templates and
the validators name an image only through a variable, never as a literal."""

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import role_var_files, tracked  # noqa: E402

PINNED = re.compile(r"@sha256:[0-9a-f]{64}$")
IMAGE_KEY = re.compile(r"^[a-z0-9_]+_(?:container|image)$")
# Values with the suffix that are no registry image: an image built on the host from a
# pinned Dockerfile, a firmware archive name, a build container's name.
LOCAL = {"slurm_exporter_container", "firmware_update_container", "slurm_deb_build_container"}
# A registry image reference in text: a known registry host, or an official image name.
LITERAL = re.compile(
    r"\b(?:nvcr\.io|docker\.io|quay\.io|ghcr\.io|gcr\.io|registry\.k8s\.io|public\.ecr\.aws)/[a-z0-9._/-]+[:@]"
    r"|(?<![\w./-])(?:python|ubuntu|debian|golang|nginx|alpine|busybox|registry|rockylinux):\d"
)


def render(value, scalars):
    """VALUE with {{ name }} references to other scalars of the same file resolved."""
    return re.sub(r"\{\{\s*([a-z0-9_]+)\s*\}\}", lambda m: str(scalars.get(m.group(1), "?")), value)


class PinnedImagesTest(unittest.TestCase):
    def test_role_defaults_and_vars(self):
        found = 0
        for path in role_var_files():
            data = yaml.safe_load(path.read_text()) or {}
            if not isinstance(data, dict):
                continue
            where = path.relative_to(ROOT)
            scalars = {k: v for k, v in data.items() if isinstance(v, str)}
            # An image per OS release: a map of pinned references, and the image a lookup in it.
            maps = {k: v for k, v in data.items() if k.endswith("_images") and isinstance(v, dict)}
            for name, refs in maps.items():
                for release, ref in refs.items():
                    found += 1
                    self.assertRegex(ref, PINNED, f"{where}: {name}[{release}] is not pinned by digest")
            for name, value in scalars.items():
                if not IMAGE_KEY.match(name) or name in LOCAL or not value:
                    continue
                if any(re.fullmatch(r"\{\{\s*" + m + r"\[[^\]]+\]\s*\}\}", value) for m in maps):
                    continue  # checked in its map above
                if re.fullmatch(r"\{\{\s*[a-z0-9_]+\s*\}\}", value) and value.strip("{} ") not in scalars:
                    continue  # another role's pin, checked where it is defined
                found += 1
                self.assertRegex(render(value, scalars), PINNED, f"{where}: {name} is not pinned by digest")
        self.assertGreater(found, 10)

    def test_no_image_literals_elsewhere(self):
        """A literal image in a playbook, task, template or script is a second copy of a
        pin, or an unpinned image upstream.py never sees."""
        files = [
            p
            for p in tracked("playbooks/**", "roles/**", "scripts/**")
            if p.suffix in {".yml", ".yaml", ".j2", ".py", ".sh", ".json"}
            and not re.search(
                r"^roles/galaxy/|/(defaults|vars|molecule|tests)/", p.relative_to(ROOT).as_posix()
            )
        ]
        self.assertGreater(len(files), 100)
        hits = [
            f"{p.relative_to(ROOT)}:{n}: {m.group(0)}"
            for p in files
            for n, line in enumerate(p.read_text(errors="ignore").splitlines(), 1)
            for m in [LITERAL.search(line)]
            if m and not line.lstrip().startswith("#")
        ]
        self.assertEqual(hits, [], "pin the image in a role's defaults and refer to the variable")

    def test_literal_pattern(self):
        for text in ("nvcr.io/nvidia/cuda:13.5.0-base", "image: python:3.15-slim", "quay.io/x/y@sha256:ab"):
            self.assertRegex(text, LITERAL)
        for text in ("https://docs.docker.com/engine/", "ansible_python_interpreter: /usr/bin/python3",
                     "python3 -m pip", "{{ ngc_ready_cuda_container }}"):  # fmt: skip
            self.assertNotRegex(text, LITERAL)

    def test_dockerfiles(self):
        for dockerfile in tracked("roles/**/Dockerfile"):
            text = dockerfile.read_text()
            for ref in re.findall(r"^ARG [A-Z_]*IMAGE=(\S+)", text, re.M):
                self.assertRegex(ref, PINNED, f"{dockerfile.relative_to(ROOT)}: {ref}")
            for ref in re.findall(r"^FROM (\S+)", text, re.M):
                if not ref.startswith("${"):
                    self.assertRegex(ref, PINNED, f"{dockerfile.relative_to(ROOT)}: FROM {ref}")


if __name__ == "__main__":
    unittest.main()
