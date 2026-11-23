"""blockdev: which disk a mount is on, and the kernel's name for a block device.

    disk_of(mount, sys_root)          -> MAJ:MIN of the whole request-based disk under MOUNT
    device_name(sys_root, "8:16")     -> "sdb" (node_exporter's device label), else MAJ:MIN

Python 3.9 or newer, standard library only.
"""

import os


class Unusable(RuntimeError):
    """A mount iocost cannot be set for; str() says why."""


def device_number(path):
    return os.stat(path).st_dev


def disk_of(mount, sys_root="/sys"):
    """MAJ:MIN of the whole request-based disk MOUNT's filesystem lives on."""
    dev = device_number(mount)
    major, minor = os.major(dev), os.minor(dev)
    node = os.path.join(sys_root, "dev/block", f"{major}:{minor}")
    if major == 0 or not os.path.isdir(node):
        raise Unusable(f"{mount}: its filesystem has no block device of its own")
    node = os.path.realpath(node)
    if os.path.exists(os.path.join(node, "partition")):
        node = os.path.dirname(node)
    for stacked in ("dm", "md"):
        if os.path.isdir(os.path.join(node, stacked)):
            raise Unusable(f"{mount}: on a {stacked} device; iocost works on a request-based disk only")
    if not os.path.isdir(os.path.join(node, "queue")):
        raise Unusable(f"{mount}: {os.path.basename(node)} has no request queue")
    with open(os.path.join(node, "dev")) as f:
        return f.read().strip()


def device_name(sys_root, device):
    """The kernel's name of block device MAJ:MIN (DEVNAME in its uevent), else MAJ:MIN."""
    try:
        with open(os.path.join(sys_root, "dev/block", device, "uevent")) as f:
            uevent = f.read()
    except OSError:
        return device
    return next(
        (line.split("=", 1)[1] for line in uevent.splitlines() if line.startswith("DEVNAME=")), device
    )
