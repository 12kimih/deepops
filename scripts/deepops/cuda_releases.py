#!/usr/bin/env python3
"""Refresh the CUDA toolkit catalog: the newest patch of every CUDA minor release, with
its runfile and NVIDIA's checksum, written into roles/nvidia_cuda_toolkit/defaults.

The catalog is data the role installs from (cuda_toolkit_oldest picks how far back a
site goes; the driver branch caps the newest). It is checked in, so a deploy is pinned
and verified; this script only regenerates it from NVIDIA's own listings:

  versions   https://developer.download.nvidia.com/compute/cuda/redist/ (redistrib_X.Y.Z.json)
  runfiles   <base>/<X.Y.Z>/docs/sidebar/md5sum.txt (NVIDIA's only checksum for them)

The runfile name cannot be derived from the version: up to 13.3 it carries the bundled
driver version (cuda_13.3.1_610.43.02_linux.run), from 13.4 it does not.

Usage:
  cuda_releases.py           rewrite the generated block
  cuda_releases.py --check   exit 1 if NVIDIA lists a newer patch or minor than the block
"""

import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "roles" / "nvidia_cuda_toolkit" / "defaults" / "main.yml"
BASE = "https://developer.download.nvidia.com/compute/cuda"
OLDEST = (11, 0)  # the catalog reaches back this far; sites choose with cuda_toolkit_oldest
BEGIN = "# BEGIN GENERATED: cuda_releases (scripts/deepops/cuda_releases.py)"
END = "# END GENERATED: cuda_releases"
TIMEOUT = 60


def fetch(url):
    with urllib.request.urlopen(url, timeout=TIMEOUT) as reply:  # noqa: S310 (fixed https URLs)
        return reply.read().decode()


def releases_in(index_html):
    """Every X.Y.Z the redist index lists, as tuples."""
    return {tuple(map(int, v)) for v in re.findall(r"redistrib_(\d+)\.(\d+)\.(\d+)\.json", index_html)}


def newest_per_minor(versions, oldest=OLDEST):
    """The highest patch of each minor at or after `oldest`, oldest minor first."""
    best = {}
    for v in versions:
        if v[:2] >= oldest and v > best.get(v[:2], (0, 0, -1)):
            best[v[:2]] = v
    return [best[m] for m in sorted(best)]


def runfile_in(md5sum_text, version):
    """(runfile, md5) of the x86_64 Linux runfile of `version` in an md5sum.txt."""
    name = re.escape(".".join(map(str, version)))
    for line in md5sum_text.splitlines():
        m = re.fullmatch(rf"([0-9a-f]{{32}})\s+(cuda_{name}(?:_[0-9.]+)?_linux\.run)", line.strip())
        if m:
            return m.group(2), m.group(1)
    raise LookupError(f"no x86_64 runfile for {name} in md5sum.txt")


def render(entries):
    lines = [BEGIN, "cuda_releases:"]
    for version, runfile, md5 in entries:
        v = ".".join(map(str, version))
        lines.append(f'  - {{version: "{v}", runfile: "{runfile}", checksum: "md5:{md5}"}}')
    return "\n".join(lines + [END])


def replace_block(text, block):
    start, end = text.index(BEGIN), text.index(END) + len(END)
    return text[:start] + block + text[end:]


def current_block(text):
    return text[text.index(BEGIN) : text.index(END) + len(END)]


def main(argv):
    versions = newest_per_minor(releases_in(fetch(f"{BASE}/redist/")))
    entries = []
    for v in versions:
        runfile, md5 = runfile_in(fetch(f"{BASE}/{'.'.join(map(str, v))}/docs/sidebar/md5sum.txt"), v)
        entries.append((v, runfile, md5))
    text = DEFAULTS.read_text()
    block = render(entries)
    if "--check" in argv:
        if current_block(text) != block:
            print(
                f"{DEFAULTS.relative_to(ROOT)}: NVIDIA lists newer CUDA releases; run {Path(__file__).name}"
            )
            return 1
        return 0
    DEFAULTS.write_text(replace_block(text, block))
    print(
        f"{len(entries)} CUDA minor releases, {versions[0][0]}.{versions[0][1]} to {versions[-1][0]}.{versions[-1][1]}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
