"""The Ubuntu kernel flavour a host boots, from its installed packages.

ansible_facts.packages | kernel_flavour(ansible_facts.kernel)  -> "generic-hwe-24.04"

Ubuntu builds each per-kernel package (linux-tools-, linux-headers-,
linux-modules-nvidia-...-) for every kernel, and a flavour metapackage of the same
name depends on the newest kernel's (linux-tools-generic-hwe-24.04). The flavour is
named by the installed linux-image-<flavour> metapackage that starts with the running
kernel's flavour (6.8.0-149-generic: generic); with several (generic and
generic-hwe-24.04), the newest, whose kernel boots by default. With none, the running
kernel's own flavour.
"""

import re

RELEASE = re.compile(r"^\d+\.\d+\.\d+-\d+-(?P<flavour>[a-z][a-z0-9.-]*)$")
META = re.compile(r"^linux-image-(?P<name>[a-z][a-z0-9.-]*)$")


def _version(version):
    return tuple(int(n) for n in re.findall(r"\d+", str(version)))


def kernel_flavour(packages, kernel):
    match = RELEASE.match(str(kernel))
    if not match:
        raise ValueError(f"{kernel!r} is not an Ubuntu kernel release (<x.y.z>-<abi>-<flavour>)")
    running = match.group("flavour")
    metas = {}
    for name, installed in (packages or {}).items():
        meta = META.match(name)
        if meta and meta.group("name") != "unsigned" and not meta.group("name").startswith("unsigned-"):
            flavour = meta.group("name")
            if flavour == running or flavour.startswith(running + "-"):
                metas[flavour] = max(_version(p.get("version", "")) for p in installed or [{}])
    return max(metas, key=lambda f: (metas[f], f)) if metas else running


class FilterModule:
    def filters(self):
        return {"kernel_flavour": kernel_flavour}
