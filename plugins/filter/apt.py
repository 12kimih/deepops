"""The Package line of an apt pin (/etc/apt/preferences.d), written one way.

    Package: {{ 'slurm-smd slurm-smd-*' | apt_pin_packages }}  -> slurm-smd:any slurm-smd-*:any
    Package: {{ ['*nvidia*'] | apt_pin_packages }}             -> *nvidia*:any

apt matches a name or glob without an architecture against the native architecture
only, and a bare "*" matched nothing at all (apt 2.7, noble): a pin meant to keep an
origin's packages out (Pin-Priority -1) still let its i386 builds in, and one i386
library from the CUDA repository (libnvidia-egl-wayland1, Multi-Arch: same) held its
amd64 twin to a version the pin refused, so the archive's driver could not install
(2026-11). ":any" matches every architecture. A pattern that names an architecture is
kept; a /regex/ cannot take one and is refused.
"""

from ansible.errors import AnsibleFilterError


def apt_pin_packages(patterns):
    """PATTERNS (a space-separated string or a list) with ":any" on each one."""
    words = patterns.split() if isinstance(patterns, str) else [str(p) for p in patterns or []]
    if not words:
        raise AnsibleFilterError("apt_pin_packages: no package pattern")
    out = []
    for word in words:
        if word.startswith("/"):
            raise AnsibleFilterError(
                f"apt_pin_packages: {word} is a regex, which matches the native architecture only"
            )
        out.append(word if ":" in word else f"{word}:any")
    return " ".join(out)


class FilterModule:
    def filters(self):
        return {"apt_pin_packages": apt_pin_packages}
