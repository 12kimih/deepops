"""slurm_gres_devices: the device files gres.conf gives a node for one GRES.

    {{ lookup('template', slurm_gres_conf_template) | slurm_gres_devices(inventory_hostname) }}

A hand-maintained gres.conf states facts about hardware (how many GPUs, which
/dev/nvidiaN) next to site policy (Cores, the Type names users request). The deploy
checks the facts against the node; the policy is the site's and is never compared.
Returns the sorted device files of the node's Name=<name> lines, or None when
AutoDetect (other than off) applies to the node, since Slurm then finds them itself.
Host lists (NodeName=gpu[1-3], File=/dev/nvidia[0-3,5]) expand with clustertools,
the one implementation of Slurm's host list syntax in this repository.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles" / "cluster_tools_lib" / "files"))
from clustertools.slurm import hostnames  # noqa: E402


def _fields(line):
    out = {}
    for token in line.split():
        key, sep, value = token.partition("=")
        if sep:
            out[key.lower()] = value
    return out


def slurm_gres_devices(text, host, name="gpu"):
    devices, autodetect = set(), "off"
    for raw in str(text).splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        fields = _fields(line)
        nodes = fields.get("nodename")
        if nodes is not None and host not in hostnames(nodes):
            continue
        if "autodetect" in fields and (nodes is not None or len(fields) == 1):
            autodetect = fields["autodetect"].lower()
        if fields.get("name") == name and "file" in fields:
            devices.update(hostnames(fields["file"]))
    return None if autodetect != "off" else sorted(devices)


class FilterModule:
    def filters(self):
        return {"slurm_gres_devices": slurm_gres_devices}
