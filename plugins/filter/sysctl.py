"""What systemd-sysctl applies at boot, from `systemd-analyze cat-config sysctl.d`.

The files are printed in the order boot applies them, each after a "# <path>" header;
the last assignment of a key wins. Keys may be written with "/" for ".".

text | sysctl_boot_values                    -> {key: {"value": v, "file": path}}
text | sysctl_boot_mismatches({key: value})  -> one line per key boot sets otherwise
"""

import re

HEADER = re.compile(r"^# (/\S+\.conf)$")


def _key(key):
    return key.strip().lstrip("-").strip().replace("/", ".")


def _value(value):
    return " ".join(str(value).split())


def sysctl_boot_values(text):
    values, path = {}, None
    for line in (text or "").splitlines():
        header = HEADER.match(line.strip())
        if header:
            path = header.group(1)
            continue
        line = line.strip()
        if not line or line[0] in "#;" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[_key(key)] = {"value": _value(value), "file": path}
    return values


def sysctl_boot_mismatches(text, wanted):
    boot = sysctl_boot_values(text)
    wrong = []
    for key, value in (wanted or {}).items():
        found = boot.get(_key(key))
        if found is None or found["value"] != _value(value):
            have = f"{found['value']} from {found['file']}" if found else "nothing"
            wrong.append(f"{_key(key)}: boot applies {have}, not {_value(value)}")
    return wrong


class FilterModule:
    def filters(self):
        return {"sysctl_boot_values": sysctl_boot_values, "sysctl_boot_mismatches": sysctl_boot_mismatches}
