"""The OS packages a Python interpreter needs for `-m venv` (roles/python_venv).

    "python3" | python_venv_packages("Debian")      -> ["python3-venv"]
    "python3.12" | python_venv_packages("Debian")   -> ["python3.12-venv"]
    "python3" | python_venv_packages("RedHat")      -> []
    "python3" | python_venv_items("Debian")         -> ["package:python3-venv"]

Debian and Ubuntu ship ensurepip, which venv needs, apart from the interpreter, one
package per interpreter name; the RHEL family ships it with the interpreter. A venv
made without it fails ("ensurepip is not available"), as the cluster bot's first deploy
did (2026-11-25). python_venv_items are the same as component items, so a role that
makes a venv records them with its own (roles/component_record keeps a package another
component still records).
"""

import posixpath


def python_venv_packages(python, os_family):
    if os_family == "Debian":
        return [f"{posixpath.basename(str(python))}-venv"]
    return []


def python_venv_items(python, os_family):
    return [f"package:{p}" for p in python_venv_packages(python, os_family)]


class FilterModule:
    def filters(self):
        return {"python_venv_packages": python_venv_packages, "python_venv_items": python_venv_items}
