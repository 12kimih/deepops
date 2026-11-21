"""CUDA releases and driver branches, from one table.

    nvidia_cuda_release_driver_branch: {"12.8": 570, "13.0": 580, ...}
    {{ table | cuda_min_driver_branch }}        -> {12: 525, 13: 580}: the oldest branch
                                                   each CUDA major runs on
    {{ table | cuda_release_for_driver(580) }}  -> "13.0": the newest CUDA release the
                                                   driver branch ships with

NVIDIA's release notes pair every CUDA release with the driver branch it ships with
(minor version compatibility: a major runs on the branch of its first release). Every
other driver/CUDA relation in the repository is computed from that one table.

The NVIDIA kernel module, as "<version> <open|proprietary>" ("" when there is none):
    {{ proc_text | nvidia_module_loaded }}           from /proc/driver/nvidia/version
    {{ version | nvidia_module_on_disk(license) }}   from modinfo -F version / -F license
A module is current when the two are equal: a version alone misses a switch between the
open and the proprietary modules of one release (Turing and newer have both).
"""

import re


def _releases(table):
    return sorted(((tuple(int(p) for p in str(k).split(".")), int(v)) for k, v in table.items()))


def cuda_min_driver_branch(table):
    out = {}
    for (major, _minor), branch in _releases(table):
        out.setdefault(major, branch)
    return out


def cuda_release_for_driver(table, branch):
    served = [release for release, needs in _releases(table) if needs <= int(branch)]
    if not served:
        raise ValueError(f"no CUDA release in the table runs on driver branch {branch}")
    major, minor = served[-1]
    return f"{major}.{minor}"


def _flavor(is_open):
    return "open" if is_open else "proprietary"


def nvidia_module_loaded(text):
    """The running module, from /proc/driver/nvidia/version ("NVRM version: NVIDIA UNIX
    Open Kernel Module for x86_64  580.95.05 ..." or "... x86_64 Kernel Module  580.95.05")."""
    match = re.search(r"Kernel Module(?: for \S+)?\s+(\d+(?:\.\d+)+)", str(text or ""))
    return f"{match.group(1)} {_flavor('Open Kernel Module' in text)}" if match else ""


def nvidia_module_on_disk(version, license_text):
    """The module modprobe would load: modinfo's version, and its license (the open
    modules are "Dual MIT/GPL", the proprietary ones "NVIDIA")."""
    version = str(version or "").strip()
    return f"{version} {_flavor('GPL' in str(license_text or ''))}" if version else ""


class FilterModule:
    def filters(self):
        return {
            "cuda_min_driver_branch": cuda_min_driver_branch,
            "cuda_release_for_driver": cuda_release_for_driver,
            "nvidia_module_loaded": nvidia_module_loaded,
            "nvidia_module_on_disk": nvidia_module_on_disk,
        }
