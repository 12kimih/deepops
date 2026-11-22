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

The Canonical archive's NVIDIA packages on a host that are not the target driver's:
    {{ installed | nvidia_driver_leftovers('580-server', 'open', false, 'amd64') }}
      -> {"before": [...], "after": [...]}
installed is dpkg's ${binary:Package} (a name, ":<arch>" on a multi-arch one). "before"
goes before the install, since apt cannot install the target beside it: another
branch, the other kernel module flavour, or another architecture (an i386 library left
by a desktop driver holds its amd64 twin to its own version). "after" goes once the
target is installed: the other userspace's metapackage of the same branch (headless or
full). Removed earlier, its dependencies, the running driver among them, would go and
come back. Everything else (the branch's shared packages, branchless ones such as
libnvidia-egl-wayland1) stays.

The packages among apt names that put an NVIDIA kernel module on disk (a preview's
installs: a reboot follows only those):
    {{ names | nvidia_module_packages }}   -> ["linux-modules-nvidia-580-server-open-generic", ...]
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


# Packages that put an NVIDIA kernel module on disk: the archive's prebuilt modules and
# the objects linked at install, and every DKMS package (the archive's nvidia-dkms-*, the
# CUDA repository's nvidia-kernel-open-dkms and nvidia-dkms-*). What the userspace,
# X driver and libraries bring needs no reboot.
_MODULE_PREFIXES = ("linux-modules-nvidia-", "linux-objects-nvidia-")


def nvidia_module_packages(names):
    """The NAMES (apt names, an arch suffix allowed) that install an NVIDIA kernel module."""
    found = []
    for package in names or []:
        name = str(package).split(":", 1)[0]
        if name.startswith(_MODULE_PREFIXES) or (name.startswith("nvidia-") and "dkms" in name):
            found.append(package)
    return found


# Packages that come in both kernel module flavours: "-open" right after the branch
# marks the open one.
_FLAVOURED = (
    "linux-modules-nvidia-",
    "linux-objects-nvidia-",
    "linux-signatures-nvidia-",
    "nvidia-kernel-source-",
    "nvidia-dkms-",
    "nvidia-headless-",
    "nvidia-driver-",
)
_BRANCH = re.compile(r"-(\d{3,}(?:-server)?)(?=-|$)")


def nvidia_driver_leftovers(installed, branch, kernel_modules, headless, arch):
    branch = str(branch)
    target_open = kernel_modules == "open"
    other_userspace = "nvidia-driver-" if headless else "nvidia-headless-"
    out = {"before": [], "after": []}
    for package in installed or []:
        name, _, package_arch = str(package).strip().partition(":")
        if not name:
            continue
        found = _BRANCH.search(name)
        if package_arch and package_arch not in (arch, "all"):
            out["before"].append(package)
        elif found and found.group(1) != branch:
            out["before"].append(package)
        elif found and name.startswith(_FLAVOURED) and name[found.end() :].startswith("-open") != target_open:
            out["before"].append(package)
        elif found and name.startswith(other_userspace):
            out["after"].append(package)
    return out


class FilterModule:
    def filters(self):
        return {
            "cuda_min_driver_branch": cuda_min_driver_branch,
            "cuda_release_for_driver": cuda_release_for_driver,
            "nvidia_module_loaded": nvidia_module_loaded,
            "nvidia_module_on_disk": nvidia_module_on_disk,
            "nvidia_driver_leftovers": nvidia_driver_leftovers,
            "nvidia_module_packages": nvidia_module_packages,
        }
