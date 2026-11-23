"""What a package removal would take that the host needs (roles/host_baseline, tasks/absent.yml).

    removal | packages_protected_taken(named, protected)  -> ["nvidia-driver-580-server-open"]

REMOVAL is every package the removal takes (apt's plan, names with or without an
architecture), NAMED the absent list as given, PROTECTED the patterns the host needs
(host_baseline_protected_packages and every package a component records, either of
which may be a shell pattern: nvidia-driver-*). A package matching a protected pattern
is taken unless the absent list names it whole: a name given whole is a decision to
remove it, a pattern that happens to match it is not. The records were compared by
exact name and a pattern's matches were never checked, so removing xserver-xorg-core
would have taken the NVIDIA driver along unrefused (audit, 2026-11-23).
"""

from fnmatch import fnmatchcase


def _base(name):
    return str(name).split(":", 1)[0]


def packages_protected_taken(removal, named, protected):
    whole = {_base(n) for n in named or [] if not any(c in str(n) for c in "*?[")}
    patterns = [str(p) for p in protected or []]
    taken = []
    for name in removal or []:
        base = _base(name)
        if base in whole or base in taken:
            continue
        if any(fnmatchcase(base, p) for p in patterns):
            taken.append(base)
    return taken


class FilterModule:
    def filters(self):
        return {"packages_protected_taken": packages_protected_taken}
