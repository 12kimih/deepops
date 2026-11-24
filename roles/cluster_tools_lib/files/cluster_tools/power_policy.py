"""power-policy: the host's power management held at full performance.

    power-policy apply CONFIG.json   set every setting found off; write the metrics
    power-policy check CONFIG.json   list the settings found off; set nothing

A compute host wants every device at speed, all the time: the CPU governor, its
hardware hint (EPP), turbo, the PCIe and SATA link power states, and every GPU. Each
sysfs setting is a file glob and the value it must hold; a file the host does not have
(no SATA host, another CPU frequency driver) is no setting there.

The GPUs (where nvidia-smi is) are held at the node's policy, never a job's:
  gpu/persistence         persistence mode on (nvidia-persistenced's: reported, not set)
  gpu/power_limit         the cap of roles/nvidia_power_limit (its file), else the default
  gpu/application_clocks  the default application clocks (set back: nvidia-smi -rac)
  gpu/clock_lock          no clock lock holding a busy GPU down (nvidia-smi reports it as
                          the applications_clocks_setting reason; set back: -rgc, -rmc)
The driver reports no locked clock while it does not limit, so a lock on an idle GPU
is seen once the GPU works.

Every PCIe link (pcie/aspm) is read where it is decided, its Link Control register in
the device's configuration space: ASPM (L0s, L1) enabled there is off. Where the kernel
controls ASPM (the link's sysfs *_aspm and *_pcipm files exist) apply turns it off
there; where the firmware keeps that control (ACPI gives the kernel none: the files are
absent and the kernel's policy file refuses writes, ARA 285) it is refused, for the BIOS
power settings to change. A choice file that lists every
option with the chosen one in brackets ("[default] performance") holds the bracketed
one. Run at boot before slurmd and again on a timer, apply puts back what anything else
changed (a job, a package, a firmware event), and says so.

Metrics, one series per setting (its glob):
  power_policy_files        files the glob matched
  power_policy_off          of them, found off at this run (apply set them)
  power_policy_refused      of them, the kernel would not set (firmware owns it)
  power_policy_last_success_timestamp_seconds
A refusal fails nothing: apply sets what it can, says on stderr what it cannot and
why, and the metrics carry it to the alert (what the firmware owns is no deploy's to
fix). A check exits 1 when something is off, refused or not.

CONFIG: sys_root (the sysfs root, /sys), textfile, settings: [{path (a glob under
sys_root), value}]; optional gpu: {nvidia_smi (the command), power_cap_file}, no GPU
settings without it. Python 3.9 or newer, standard library only.
"""

import glob
import os
import re
import sys
import time

import shutil

from . import config, proc
from .files import write_textfile
from .prom import series

CONFIG_KEYS = {"sys_root": str, "textfile": str, "settings": list}
CONFIG_OPTIONAL = {"gpu": dict}
CHOSEN = re.compile(r"\[([^]]+)\]")
NVSMI_TIMEOUT = 60  # one nvidia-smi call; a hung driver must not hold the boot
GPU_FIELDS = ("index", "persistence_mode", "power.limit", "power.default_limit", "clocks.applications.graphics",
              "clocks.default_applications.graphics", "clocks.applications.memory",
              "clocks.default_applications.memory", "clocks_event_reasons.applications_clocks_setting")  # fmt: skip
GPU_SETTINGS = ("gpu/persistence", "gpu/power_limit", "gpu/application_clocks", "gpu/clock_lock")
WATTS_SLACK = 0.5  # nvidia-smi rounds a limit it was given


def load_config(path):
    cfg = config.require(path, CONFIG_KEYS, CONFIG_OPTIONAL)
    gpu = cfg.get("gpu")
    if gpu is not None and (
        set(gpu) != {"nvidia_smi", "power_cap_file"} or not all(isinstance(v, str) for v in gpu.values())
    ):
        raise config.ConfigError(f"{path}: gpu needs exactly nvidia_smi and power_cap_file, strings")
    for i, setting in enumerate(cfg["settings"]):
        where = f"{path}: settings[{i}]"
        if not isinstance(setting, dict) or set(setting) != {"path", "value"}:
            raise config.ConfigError(f"{where}: needs exactly path and value")
        if not all(isinstance(setting[k], str) and setting[k] for k in ("path", "value")):
            raise config.ConfigError(f"{where}: path and value are non-empty strings")
        if setting["path"].startswith("/") or ".." in setting["path"].split("/"):
            raise config.ConfigError(f"{where}: path is relative to sys_root, without ..")
    return cfg


def held(path):
    """The value PATH holds: a choice file's bracketed option, else its text."""
    with open(path) as f:
        text = f.read().strip()
    chosen = CHOSEN.search(text)
    return chosen.group(1) if chosen else text


def run(cfg, fix, out=sys.stdout):
    """Each setting's files, off and refused counts; with FIX, the off ones are set."""
    rows = []
    for setting in cfg["settings"]:
        want, files, off, refused = setting["value"], 0, 0, 0
        for path in sorted(glob.glob(os.path.join(cfg["sys_root"], setting["path"]))):
            files += 1
            try:
                have = held(path)
            except OSError as e:
                print(f"power-policy: {path}: {e.strerror}", file=sys.stderr)
                refused += 1
                continue
            if have == want:
                continue
            off += 1
            if not fix:
                print(f"{path}: {have}, wants {want}", file=out)
                continue
            try:
                with open(path, "w") as f:
                    f.write(want)
                now_held = held(path)
            except OSError as e:
                now_held = f"{have} ({e.strerror})"
            if now_held == want:
                print(f"{path}: {have} -> {want}", file=out)
            else:
                print(f"power-policy: {path}: the kernel keeps {now_held}, not {want}", file=sys.stderr)
                refused += 1
        rows.append((setting["path"], files, off, refused))
    return rows


def _number(text):
    try:
        return float(text)
    except ValueError:
        return None  # [N/A], [Not Supported]


def power_cap(path):
    """The node's GPU power cap in watts (roles/nvidia_power_limit's file), or None."""
    try:
        with open(path) as f:
            for line in f:
                if line.startswith("NVIDIA_POWER_LIMIT_WATTS="):
                    return _number(line.split("=", 1)[1].strip())
    except OSError:
        pass
    return None


def gpu_rows(nvidia_smi):
    """{index: {field: text}} of every GPU nvidia-smi lists; raises OSError when it fails."""
    rc, out, err = proc.outcome([nvidia_smi, "--query-gpu=" + ",".join(GPU_FIELDS), "--format=csv,noheader,nounits"],
                                timeout=NVSMI_TIMEOUT)  # fmt: skip
    if rc != 0:
        raise OSError(f"nvidia-smi --query-gpu failed ({rc}): {err or out}")
    rows = {}
    for line in out.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) == len(GPU_FIELDS):
            rows[cells[0]] = dict(zip(GPU_FIELDS, cells))
    return rows


def gpu_off(row, cap):
    """{setting: (holds, wants)} for each of GPU_SETTINGS this GPU is off on."""
    off = {}
    if row["persistence_mode"] != "Enabled":
        off["gpu/persistence"] = (row["persistence_mode"], "Enabled")
    limit, default = _number(row["power.limit"]), _number(row["power.default_limit"])
    if limit is not None and default is not None:
        want = min(default, cap) if cap else default
        if abs(limit - want) > WATTS_SLACK:
            off["gpu/power_limit"] = (f"{limit:g} W", f"{want:g} W")
    for kind in ("graphics", "memory"):
        now, base = row[f"clocks.applications.{kind}"], row[f"clocks.default_applications.{kind}"]
        if _number(now) is not None and _number(base) is not None and now != base:
            off["gpu/application_clocks"] = (f"{kind} {now} MHz", f"{base} MHz")
    if row["clocks_event_reasons.applications_clocks_setting"] == "Active":
        off["gpu/clock_lock"] = ("locked", "unlocked")
    return off


def gpu_fixes(index, setting, row, cap):
    """The nvidia-smi arguments that put SETTING back on GPU INDEX ([] for none: the
    persistence mode is nvidia-persistenced's)."""
    if setting == "gpu/power_limit":
        default = _number(row["power.default_limit"])
        return [["-i", index, "-pl", f"{min(default, cap) if cap else default:g}"]]
    if setting == "gpu/application_clocks":
        return [["-i", index, "-rac"]]
    if setting == "gpu/clock_lock":
        return [["-i", index, "-rgc"], ["-i", index, "-rmc"]]
    return []


def run_gpus(gpu, fix, out=sys.stdout):
    """The GPU rows of a run: (setting, GPUs, off, refused) for each of GPU_SETTINGS;
    [] where there is no nvidia-smi."""
    nvidia_smi = shutil.which(gpu["nvidia_smi"])
    if not nvidia_smi:
        return []
    cap = power_cap(gpu["power_cap_file"])
    try:
        rows = gpu_rows(nvidia_smi)
    except OSError as e:
        print(f"power-policy: {e}", file=sys.stderr)
        return [(setting, 0, 0, 1) for setting in GPU_SETTINGS]
    count = {setting: [len(rows), 0, 0] for setting in GPU_SETTINGS}
    for index, row in sorted(rows.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        for setting, (have, want) in gpu_off(row, cap).items():
            count[setting][1] += 1
            if not fix:
                print(f"GPU {index} {setting}: {have}, wants {want}", file=out)
                continue
            fixes = gpu_fixes(index, setting, row, cap)
            # Status 3 is "not supported on this GPU": nothing to put back there.
            failed = [
                a for a in fixes if proc.outcome([nvidia_smi, *a], timeout=NVSMI_TIMEOUT)[0] not in (0, 3)
            ]
            if not fixes or failed:
                why = (
                    "nvidia-persistenced keeps it"
                    if not fixes
                    else "nvidia-smi " + " ".join(failed[0]) + " failed"
                )
                print(f"power-policy: GPU {index} {setting}: {have}, not {want} ({why})", file=sys.stderr)
                count[setting][2] += 1
            else:
                print(f"GPU {index} {setting}: {have} -> {want}", file=out)
    return [(setting, *count[setting]) for setting in GPU_SETTINGS]


PCI_EXPRESS = 0x10  # the PCI Express capability's ID
LINK_CONTROL = 0x10  # its Link Control register's offset
ASPM_BITS = 0x3  # Link Control bits 1:0, ASPM L0s and L1 enabled


def link_control(config_bytes):
    """The Link Control register of a device's configuration space, or None (no PCI
    Express capability, or only the first 64 bytes readable, as for a non-root user)."""
    if len(config_bytes) < 0x40 or not config_bytes[0x06] & 0x10:  # no capability list
        return None
    pointer, seen = config_bytes[0x34] & 0xFC, set()
    while pointer and pointer not in seen and pointer + LINK_CONTROL + 2 <= len(config_bytes):
        seen.add(pointer)
        if config_bytes[pointer] == PCI_EXPRESS:
            return int.from_bytes(config_bytes[pointer + LINK_CONTROL : pointer + LINK_CONTROL + 2], "little")
        pointer = config_bytes[pointer + 1] & 0xFC
    return None


def run_links(sys_root, fix, out=sys.stdout):
    """The PCIe row of a run: ("pcie/aspm", links, off, refused)."""
    links = off = refused = 0
    for device in sorted(glob.glob(os.path.join(sys_root, "bus/pci/devices/*"))):
        try:
            with open(os.path.join(device, "config"), "rb") as f:
                control = link_control(f.read())
        except OSError:
            control = None
        if control is None:
            continue
        links += 1
        if not control & ASPM_BITS:
            continue
        off += 1
        name = os.path.basename(device)
        knobs = sorted(
            glob.glob(os.path.join(device, "link/*_aspm")) + glob.glob(os.path.join(device, "link/*_pcipm"))
        )
        if not fix:
            print(f"PCIe {name} pcie/aspm: enabled, wants disabled", file=out)
            continue
        if not knobs:
            print(f"power-policy: PCIe {name} pcie/aspm: enabled, the firmware keeps ASPM (BIOS power settings)",
                  file=sys.stderr)  # fmt: skip
            refused += 1
            continue
        try:
            for knob in knobs:
                with open(knob, "w") as f:
                    f.write("0")
            print(f"PCIe {name} pcie/aspm: enabled -> disabled", file=out)
        except OSError as e:
            print(f"power-policy: PCIe {name} pcie/aspm: the kernel refuses ({e.strerror})", file=sys.stderr)
            refused += 1
    return [("pcie/aspm", links, off, refused)]


def textfile(rows, now):
    lines = []
    for name, help_text, column in (
        ("power_policy_files", "Files (GPUs) the setting covers.", 1),
        ("power_policy_off", "Files (GPUs) found off the setting at the last run.", 2),
        ("power_policy_refused", "Files (GPUs) that could not be set to the setting.", 3),
    ):
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
        lines += [series(name, {"setting": row[0]}, row[column]) for row in rows]
    lines += [
        "# HELP power_policy_last_success_timestamp_seconds When an apply last ran through every setting.",
        "# TYPE power_policy_last_success_timestamp_seconds gauge",
        series("power_policy_last_success_timestamp_seconds", {}, int(now)),
    ]
    return "\n".join(lines) + "\n"


def rows_of(cfg, fix, out):
    """Every setting's row: the sysfs settings, the PCIe links, the GPUs."""
    gpus = run_gpus(cfg["gpu"], fix=fix, out=out) if cfg.get("gpu") else []
    return run(cfg, fix=fix, out=out) + run_links(cfg["sys_root"], fix=fix, out=out) + gpus


def apply(cfg, out=sys.stdout, now=None):
    write_textfile(cfg["textfile"], textfile(rows_of(cfg, True, out), time.time() if now is None else now))
    return 0


def check(cfg, out=sys.stdout):
    return int(any(row[2] or row[3] for row in rows_of(cfg, False, out)))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    commands = {"apply": apply, "check": check}
    if len(argv) != 2 or argv[0] not in commands:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    try:
        cfg = load_config(argv[1])
    except config.ConfigError as e:
        print(f"power-policy: {e}", file=sys.stderr)
        return 2
    return commands[argv[0]](cfg)


if __name__ == "__main__":
    sys.exit(main())
