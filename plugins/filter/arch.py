"""A machine's architecture in each vocabulary that names it, from ansible_facts.architecture
(uname -m: x86_64, aarch64, ...). One table, so no role keeps its own mapping.

    {{ ansible_facts.architecture | deb_architecture }}   -> amd64, arm64     (dpkg, .deb names)
    {{ ansible_facts.architecture | rpm_architecture }}   -> x86_64, aarch64  (rpm, .rpm names)
    {{ ansible_facts.architecture | multiarch_tuple }}    -> x86_64-linux-gnu (/usr/lib/<tuple>)
    {{ ansible_facts.architecture | cuda_architecture }}  -> x86_64, sbsa     (NVIDIA's CUDA repos)

The tuple is not derivable from the name (armv7l is arm-linux-gnueabihf); an
architecture the table lacks keeps its own name (and <name>-linux-gnu as a tuple).
"""

# uname -m (Ansible reports arm64 on some systems for aarch64) -> each vocabulary.
_TABLE = {
    "x86_64": {"deb": "amd64", "rpm": "x86_64", "tuple": "x86_64-linux-gnu", "cuda": "x86_64"},
    "aarch64": {"deb": "arm64", "rpm": "aarch64", "tuple": "aarch64-linux-gnu", "cuda": "sbsa"},
    "arm64": {"deb": "arm64", "rpm": "aarch64", "tuple": "aarch64-linux-gnu", "cuda": "sbsa"},
    "armv7l": {"deb": "armhf", "rpm": "armv7hl", "tuple": "arm-linux-gnueabihf", "cuda": "armv7l"},
    "ppc64le": {"deb": "ppc64el", "rpm": "ppc64le", "tuple": "powerpc64le-linux-gnu", "cuda": "ppc64le"},
}


def _name(machine, vocabulary, default):
    machine = str(machine)
    return _TABLE.get(machine, {}).get(vocabulary, default(machine))


def deb_architecture(machine):
    return _name(machine, "deb", str)


def rpm_architecture(machine):
    return _name(machine, "rpm", str)


def multiarch_tuple(machine):
    return _name(machine, "tuple", lambda m: f"{m}-linux-gnu")


def cuda_architecture(machine):
    return _name(machine, "cuda", str)


class FilterModule:
    def filters(self):
        return {
            "deb_architecture": deb_architecture,
            "rpm_architecture": rpm_architecture,
            "multiarch_tuple": multiarch_tuple,
            "cuda_architecture": cuda_architecture,
        }
