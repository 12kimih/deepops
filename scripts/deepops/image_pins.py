#!/usr/bin/env python3
"""The container images the roles pin, and what their tags point at now.

    image_pins.py list               every pinned image (repo:tag@sha256:...) and where
    image_pins.py check              fail when a pinned tag now points at another digest
    image_pins.py pin IMAGE:TAG ...  print IMAGE:TAG@<index digest>, the line to pin

A role pins an image by tag and digest, so a deploy pulls exactly what was reviewed. To
move to a new release, `pin` the new tag and put the printed line in the role's defaults.
The digest is the registry's own (Docker-Content-Digest) for the multi-platform index,
read with the anonymous token flow every registry here offers (Docker Hub, quay.io,
nvcr.io, ghcr.io). Nothing is pulled.
"""

import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PINNED = re.compile(r"""(?<![\w.:/-])((?:[\w.-]+(?::\d+)?/)?[\w./-]+:[\w.-]+)@(sha256:[0-9a-f]{64})""")
ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])  # fmt: skip


def split(ref):
    """(registry host, repository, tag) of IMAGE:TAG, Docker Hub's rules for a bare name."""
    name, tag = ref.rsplit(":", 1)
    first = name.split("/", 1)[0]
    if "/" in name and ("." in first or ":" in first or first == "localhost"):
        host, repo = name.split("/", 1)
    else:
        host, repo = "registry-1.docker.io", name if "/" in name else f"library/{name}"
    if host == "docker.io":
        host = "registry-1.docker.io"
    return host, repo, tag


def _request(url, token=None):
    headers = {"Accept": ACCEPT, "User-Agent": "deepops-image-pins"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers=headers), timeout=30)


def digest(ref):
    """The index digest IMAGE:TAG points at now."""
    host, repo, tag = split(ref)
    url = f"https://{host}/v2/{repo}/manifests/{tag}"
    try:
        response = _request(url)
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise
        # The registry names where to get an anonymous token for this repository.
        challenge = dict(re.findall(r'(\w+)="([^"]*)"', e.headers.get("WWW-Authenticate", "")))
        query = urllib.parse.urlencode({k: v for k, v in challenge.items() if k in ("service", "scope")})
        with urllib.request.urlopen(f"{challenge['realm']}?{query}", timeout=30) as t:
            body = json.load(t)
        response = _request(url, body.get("token") or body.get("access_token"))
    return response.headers["Docker-Content-Digest"]


def pins():
    """[(image:tag, digest, file:line)] for every pin in the roles' defaults, vars and
    Dockerfiles (a build stage's base image)."""
    files = subprocess.run(
        ["git", "ls-files", "roles/*/defaults/*", "roles/*/vars/*", "roles/**/Dockerfile"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    found = []
    for rel in files:
        if rel.startswith("roles/galaxy/"):
            continue
        for number, line in enumerate((ROOT / rel).read_text(errors="ignore").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for ref, sha in PINNED.findall(line):
                found.append((ref, sha, f"{rel}:{number}"))
    return found


def main(argv):
    if not argv or argv[0] not in ("list", "check", "pin"):
        print(__doc__, file=sys.stderr)
        return 2
    if argv[0] == "pin":
        for ref in argv[1:]:
            print(f"{ref}@{digest(ref)}")
        return 0
    failed = 0
    for ref, sha, where in pins():
        if argv[0] == "list":
            print(f"{ref}@{sha}  {where}")
            continue
        now = digest(ref)
        if now != sha:
            failed += 1
            print(f"{where}: {ref} now points at {now}, pinned {sha}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
