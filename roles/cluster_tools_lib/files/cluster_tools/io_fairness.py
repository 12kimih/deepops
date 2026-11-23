"""io-fairness: blk-iocost's cost model and latency targets for the disks under given mounts.

    io-fairness apply CONFIG.json    write each device's model, then its QoS (enable as set)
    io-fairness reset CONFIG.json    turn iocost off and give each device back the kernel's model
    io-fairness show CONFIG.json     what the kernel holds for each device

iocost shares a disk by cgroup weight (io.weight; systemd's IOWeight=), charging each
I/O what the model says the device pays for it. The model is the device's own,
measured (scripts/deepops/io_model.py, ctrl=user): ctrl=auto gives a rotational disk
the generic single-HDD model, which caps a RAID at a fraction of what it does. The QoS
latency targets let iocost scale the model down while the device is slower than
measured and up while faster (vrate, within min..max). Each mount is resolved to the
whole disk under it (a partition's parent) at every run, so a renumbered device after a
reboot is found again; a mount on a stacked device (dm, md, a filesystem with no block
device) is refused: iocost works on a request-based disk only.

I/O from the root cgroup (kernel threads: nfsd, the flusher of the root's own pages)
is never throttled by iocost; it competes only through the latency targets. The
textfile says per device whether iocost is on (io_fairness_enabled), for the dashboards.

CONFIG: cgroup_root, sys_root (the sysfs root, /sys), textfile, devices: [{mount,
enable, model: {rbps, rseqiops, rrandiops, wbps, wseqiops, wrandiops}, qos: {rpct,
rlat, wpct, wlat, min, max}}]. Python 3.9 or newer, standard library only.
"""

import os
import sys

from . import config
from .files import write_textfile

CONFIG_KEYS = {"cgroup_root": str, "sys_root": str, "textfile": str, "devices": list}
MODEL = ("rbps", "rseqiops", "rrandiops", "wbps", "wseqiops", "wrandiops")
QOS = ("rpct", "rlat", "wpct", "wlat", "min", "max")


class Unusable(RuntimeError):
    """A mount iocost cannot be set for; str() says why."""


def load_config(path):
    cfg = config.require(path, CONFIG_KEYS)
    for i, dev in enumerate(cfg["devices"]):
        where = f"{path}: devices[{i}]"
        if not isinstance(dev, dict) or set(dev) != {"mount", "enable", "model", "qos"}:
            raise config.ConfigError(f"{where}: needs exactly mount, enable, model and qos")
        if not isinstance(dev["mount"], str) or not dev["mount"].startswith("/"):
            raise config.ConfigError(f"{where}: mount is not an absolute path")
        if not isinstance(dev["enable"], bool):
            raise config.ConfigError(f"{where}: enable is not true or false")
        for part, keys in (("model", MODEL), ("qos", QOS)):
            values = dev[part]
            if not isinstance(values, dict) or set(values) != set(keys):
                raise config.ConfigError(f"{where}: {part} needs exactly {', '.join(keys)}")
            bad = [k for k in keys if isinstance(values[k], bool) or not isinstance(values[k], (int, float))
                   or values[k] <= 0]  # fmt: skip
            if bad:
                raise config.ConfigError(f"{where}: {part} {', '.join(bad)}: not a positive number")
        qos = dev["qos"]
        if qos["rpct"] > 100 or qos["wpct"] > 100 or qos["min"] > qos["max"]:
            raise config.ConfigError(f"{where}: qos needs rpct and wpct at most 100 and min at most max")
    return cfg


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


def number(value):
    return f"{value:.2f}" if isinstance(value, float) else str(value)


def model_line(device, model):
    return f"{device} ctrl=user model=linear " + " ".join(f"{k}={number(model[k])}" for k in MODEL)


def qos_line(device, enable, qos):
    return f"{device} enable={int(enable)} ctrl=user " + " ".join(f"{k}={number(qos[k])}" for k in QOS)


def held(cgroup_root, name, device):
    """The kernel's line for DEVICE in io.cost.NAME, or ""."""
    with open(os.path.join(cgroup_root, f"io.cost.{name}")) as f:
        return next((line.strip() for line in f if line.split()[:1] == [device]), "")


def write(cgroup_root, name, line):
    with open(os.path.join(cgroup_root, f"io.cost.{name}"), "w") as f:
        f.write(line + "\n")


def textfile(rows):
    lines = ["# HELP io_fairness_enabled Whether iocost shares the disk under the mount by cgroup weight.",
             "# TYPE io_fairness_enabled gauge"]  # fmt: skip
    for mount, device, on in rows:
        lines.append(f'io_fairness_enabled{{mount="{mount}",device="{device}"}} {int(on)}')
    return "\n".join(lines) + "\n"


def apply(cfg, reset=False, out=sys.stdout):
    """Write every device; 1 if any could not be set or reads back otherwise."""
    status, rows = 0, []
    for dev in cfg["devices"]:
        try:
            device = disk_of(dev["mount"], cfg["sys_root"])
        except (OSError, Unusable) as e:
            print(f"io-fairness: {e}", file=sys.stderr)
            status = 1
            continue
        enable = dev["enable"] and not reset
        try:
            if reset:
                write(cfg["cgroup_root"], "qos", f"{device} enable=0")
                write(cfg["cgroup_root"], "model", f"{device} ctrl=auto")
            else:
                # The model first: enabling with the old model would charge by it meanwhile.
                write(cfg["cgroup_root"], "model", model_line(device, dev["model"]))
                write(cfg["cgroup_root"], "qos", qos_line(device, enable, dev["qos"]))
            now = held(cfg["cgroup_root"], "qos", device)
        except OSError as e:
            print(f"io-fairness: {dev['mount']} ({device}): {e.strerror}", file=sys.stderr)
            status = 1
            continue
        if f"enable={int(enable)}" not in now.split():
            print(f"io-fairness: {dev['mount']} ({device}): the kernel holds '{now}'", file=sys.stderr)
            status = 1
        rows.append((dev["mount"], device, enable))
        print(f"{dev['mount']} ({device}): iocost {'on' if enable else 'off'}", file=out)
    write_textfile(cfg["textfile"], textfile(rows))
    return status


def show(cfg, out=sys.stdout):
    for dev in cfg["devices"]:
        try:
            device = disk_of(dev["mount"], cfg["sys_root"])
            print(f"{dev['mount']} ({device})", file=out)
            for name in ("model", "qos"):
                print(f"  {name}: {held(cfg['cgroup_root'], name, device) or '(none)'}", file=out)
        except (OSError, Unusable) as e:
            print(f"io-fairness: {e}", file=sys.stderr)
            return 1
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    commands = {"apply": apply, "reset": lambda cfg: apply(cfg, reset=True), "show": show}
    if len(argv) != 2 or argv[0] not in commands:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    try:
        cfg = load_config(argv[1])
    except config.ConfigError as e:
        print(f"io-fairness: {e}", file=sys.stderr)
        return 2
    return commands[argv[0]](cfg)


if __name__ == "__main__":
    sys.exit(main())
