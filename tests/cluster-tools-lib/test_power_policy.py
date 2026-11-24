"""cluster_tools.power_policy: which files a setting covers, what a run sets, refuses
and reports. sysfs is a stand-in directory."""

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
from cluster_tools import config  # noqa: E402
from cluster_tools import power_policy as policy  # noqa: E402
from cluster_tools import prom  # noqa: E402

GOVERNOR = "devices/system/cpu/cpu*/cpufreq/scaling_governor"
ASPM = "module/pcie_aspm/parameters/policy"
SATA = "class/scsi_host/host*/link_power_management_policy"


class Tree:
    """Two CPUs (one at powersave), the ASPM choice file at default, no SATA hosts."""

    def __init__(self, d):
        self.sys = Path(d, "sys")
        for cpu, gov in (("cpu0", "performance"), ("cpu1", "powersave")):
            self.write(f"devices/system/cpu/{cpu}/cpufreq/scaling_governor", gov)
        self.write(ASPM, "[default] performance powersave powersupersave")
        self.cfg = {
            "sys_root": str(self.sys),
            "textfile": f"{d}/prom/power-policy.prom",
            "settings": [{"path": GOVERNOR, "value": "performance"}, {"path": ASPM, "value": "performance"},
                         {"path": SATA, "value": "max_performance"}],
        }  # fmt: skip

    def write(self, rel, text):
        path = self.sys / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n")

    def read(self, rel):
        return (self.sys / rel).read_text().strip()

    def metrics(self):
        return {k: int(v) for k, v in prom.parse(Path(self.cfg["textfile"]).read_text()).items()}


def aspm_choice_write(real_open):
    """The kernel's ASPM file: a write of an option makes it the bracketed one."""

    def fake_open(path, mode="r", *args, **kw):
        if str(path).endswith(ASPM) and mode == "w":
            target = Path(path)

            class Choice(io.StringIO):
                def close(self):
                    opts = ["default", "performance", "powersave", "powersupersave"]
                    chosen = self.getvalue().strip()
                    target.write_text(" ".join(f"[{o}]" if o == chosen else o for o in opts) + "\n")
                    super().close()

            return Choice()
        return real_open(path, mode, *args, **kw)

    return fake_open


class Config(unittest.TestCase):
    def check(self, settings):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "c.json")
            path.write_text(json.dumps({"sys_root": "/sys", "textfile": "/x.prom", "settings": settings}))
            return policy.load_config(str(path))

    def test_a_setting_is_a_relative_glob_and_a_value(self):
        self.assertEqual(len(self.check([{"path": GOVERNOR, "value": "performance"}])["settings"]), 1)
        for bad in ({"path": "/sys/x", "value": "a"}, {"path": "../etc/x", "value": "a"},
                    {"path": "x", "value": ""}, {"path": "x"}, {"path": "x", "value": "a", "extra": 1}):  # fmt: skip
            with self.subTest(bad=bad), self.assertRaises(config.ConfigError):
                self.check([bad])


class Apply(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.tree = Tree(self.dir.name)
        real_open = open
        patcher = mock.patch("builtins.open", aspm_choice_write(real_open))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.dir.cleanup)

    def test_check_lists_what_is_off_and_sets_nothing(self):
        out = io.StringIO()
        self.assertEqual(policy.check(self.tree.cfg, out=out), 1)
        self.assertIn("cpu1/cpufreq/scaling_governor: powersave, wants performance", out.getvalue())
        self.assertIn("policy: default, wants performance", out.getvalue())
        self.assertEqual(self.tree.read("devices/system/cpu/cpu1/cpufreq/scaling_governor"), "powersave")

    def test_apply_sets_what_is_off_and_reports_it(self):
        self.assertEqual(policy.apply(self.tree.cfg, out=io.StringIO(), now=100), 0)
        self.assertEqual(self.tree.read("devices/system/cpu/cpu1/cpufreq/scaling_governor"), "performance")
        self.assertEqual(self.tree.read(ASPM), "default [performance] powersave powersupersave")
        m = self.tree.metrics()
        self.assertEqual(m[prom.key("power_policy_files", setting=GOVERNOR)], 2)
        self.assertEqual(m[prom.key("power_policy_off", setting=GOVERNOR)], 1)
        self.assertEqual(m[prom.key("power_policy_off", setting=ASPM)], 1)
        self.assertEqual(m[prom.key("power_policy_files", setting=SATA)], 0)
        self.assertEqual(m[prom.key("power_policy_last_success_timestamp_seconds")], 100)
        self.assertEqual(policy.check(self.tree.cfg, out=io.StringIO()), 0)

    def test_a_setting_the_kernel_keeps_is_refused_and_reported_never_failing_the_run(self):
        real_open = open

        def refusing(path, mode="r", *args, **kw):
            if mode == "w" and str(path).endswith("cpu1/cpufreq/scaling_governor"):
                raise PermissionError(1, "Operation not permitted")
            return real_open(path, mode, *args, **kw)

        with mock.patch("builtins.open", refusing), mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(policy.apply(self.tree.cfg, out=io.StringIO(), now=1), 0)
        self.assertIn("the kernel keeps powersave (Operation not permitted), not performance", err.getvalue())
        self.assertEqual(self.tree.metrics()[prom.key("power_policy_refused", setting=GOVERNOR)], 1)


def config_space(aspm):
    """A device's configuration space with a PCI Express capability at 0x40 behind a
    power management one at 0x50, its Link Control ASPM bits as given."""
    space = bytearray(256)
    space[0x06] = 0x10  # a capability list
    space[0x34] = 0x50
    space[0x50:0x52] = bytes([0x01, 0x40])  # power management, next 0x40
    space[0x40:0x42] = bytes([0x10, 0x00])  # PCI Express, last
    space[0x40 + 0x10] = aspm
    return bytes(space)


class Links(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.sys = Path(self.dir.name)

    def device(self, name, aspm, knobs=(), short=False):
        d = self.sys / "bus/pci/devices" / name
        (d / "link").mkdir(parents=True)
        (d / "config").write_bytes(config_space(aspm)[: 64 if short else 256])
        for knob in knobs:
            (d / "link" / knob).write_text("1\n")
        return d

    def test_the_register_is_found_through_the_capability_list(self):
        self.assertEqual(policy.link_control(config_space(0x2)) & policy.ASPM_BITS, 0x2)
        self.assertIsNone(policy.link_control(config_space(0x2)[:64]), "first 64 bytes only: unknown")
        self.assertIsNone(policy.link_control(bytes(256)), "no capability list")

    def test_kernel_controlled_links_are_set_off_and_firmware_ones_refused(self):
        kernel = self.device("0000:01:00.0", 0x2, knobs=("l1_aspm", "l0s_aspm", "l1_1_pcipm"))
        self.device("0000:02:00.0", 0x3)  # the firmware keeps ASPM: no knobs
        self.device("0000:03:00.0", 0x0)
        self.device("0000:04:00.0", 0x3, short=True)  # unreadable: not counted
        out = io.StringIO()
        with mock.patch("sys.stderr", io.StringIO()) as err:
            rows = policy.run_links(str(self.sys), fix=True, out=out)
        self.assertEqual(rows, [("pcie/aspm", 3, 2, 1)])
        self.assertEqual({k.name: k.read_text() for k in (kernel / "link").iterdir()},
                         {"l1_aspm": "0", "l0s_aspm": "0", "l1_1_pcipm": "0"})  # fmt: skip
        self.assertIn("0000:01:00.0 pcie/aspm: enabled -> disabled", out.getvalue())
        self.assertIn("0000:02:00.0 pcie/aspm: enabled, the firmware keeps ASPM", err.getvalue())

    def test_check_lists_enabled_links_and_sets_nothing(self):
        d = self.device("0000:01:00.0", 0x2, knobs=("l1_aspm",))
        out = io.StringIO()
        self.assertEqual(policy.run_links(str(self.sys), fix=False, out=out), [("pcie/aspm", 1, 1, 0)])
        self.assertEqual((d / "link/l1_aspm").read_text(), "1\n")
        self.assertIn("enabled, wants disabled", out.getvalue())


# nvidia-smi over files in $FAKE: "rows" answers --query-gpu (one CSV row per GPU),
# "rc-query" the status of the query, "rc-<flag>" the status of a setting call (-pl, -rac, -rgc, -rmc); each call is
# logged to "calls".
NVIDIA_SMI = """#!/bin/sh
echo "$*" >> "$FAKE/calls"
case "$1" in --query-gpu=*) [ -f "$FAKE/rc-query" ] && exit "$(cat "$FAKE/rc-query")"; cat "$FAKE/rows"; exit 0 ;; esac
flag="$3"; [ -f "$FAKE/rc$flag" ] && exit "$(cat "$FAKE/rc$flag")"
exit 0
"""
ROW = "{i}, {pm}, {pl}, 300.00, {ag}, 2490, {am}, 9001, {lock}\n"


class Gpus(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        d = Path(self.dir.name)
        (d / "bin").mkdir()
        smi = d / "bin" / "nvidia-smi"
        smi.write_text(NVIDIA_SMI)
        smi.chmod(smi.stat().st_mode | stat.S_IEXEC)
        self.cap = d / "nvidia-power-limit.conf"
        self.gpu = {"nvidia_smi": str(smi), "power_cap_file": str(self.cap)}
        patcher = mock.patch.dict(os.environ, {"FAKE": str(d)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.d = d

    def rows(self, *gpus):
        (self.d / "rows").write_text("".join(ROW.format(**g) for g in gpus))

    def card(self, i, pm="Enabled", pl="300.00", ag="2490", am="9001", lock="Not Active"):
        return {"i": i, "pm": pm, "pl": pl, "ag": ag, "am": am, "lock": lock}

    def calls(self):
        f = self.d / "calls"
        return [c for c in f.read_text().splitlines() if not c.startswith("--query")] if f.exists() else []

    def run_gpus(self, fix):
        with mock.patch("sys.stderr", io.StringIO()) as err:
            rows = policy.run_gpus(self.gpu, fix=fix, out=io.StringIO())
        return {r[0]: r[1:] for r in rows}, err.getvalue()

    def test_a_gpu_at_the_policy_is_left_alone(self):
        self.rows(self.card("0"), self.card("1"))
        rows, _ = self.run_gpus(fix=True)
        self.assertEqual(rows["gpu/power_limit"], (2, 0, 0))
        self.assertEqual(self.calls(), [])

    def test_the_cap_file_is_the_target_and_what_is_off_is_set_back(self):
        self.cap.write_text("NVIDIA_POWER_LIMIT_WATTS=250\n")
        self.rows(self.card("0", pl="300.00", ag="1500", lock="Active"))
        rows, _ = self.run_gpus(fix=True)
        self.assertEqual(rows["gpu/power_limit"], (1, 1, 0))
        self.assertEqual(rows["gpu/application_clocks"], (1, 1, 0))
        self.assertEqual(rows["gpu/clock_lock"], (1, 1, 0))
        self.assertEqual(self.calls(), ["-i 0 -pl 250", "-i 0 -rac", "-i 0 -rgc", "-i 0 -rmc"])

    def test_a_cap_above_the_default_is_the_target(self):
        # roles/nvidia_power_limit keeps the cap within the card's range, above its
        # default too: holding the default would undo it every run.
        self.cap.write_text("NVIDIA_POWER_LIMIT_WATTS=350\n")
        self.rows(self.card("0", pl="350.00"))
        self.assertEqual(self.run_gpus(fix=True)[0]["gpu/power_limit"], (1, 0, 0))
        self.rows(self.card("0", pl="300.00"))
        self.assertEqual(self.run_gpus(fix=True)[0]["gpu/power_limit"], (1, 1, 0))

    def test_a_driver_without_gpus_has_no_gpu_settings(self):
        (self.d / "rc-query").write_text("6")  # No devices were found
        rows, _ = self.run_gpus(fix=True)
        self.assertEqual(rows["gpu/power_limit"], (0, 0, 0))

    def test_persistence_is_reported_never_set_and_a_failed_call_is_refused(self):
        (self.d / "rc-pl").write_text("1")
        (self.d / "rc-rmc").write_text("3")  # not supported here: nothing to put back
        self.rows(self.card("0", pm="Disabled", pl="250.00", lock="Active"))
        rows, err = self.run_gpus(fix=True)
        self.assertEqual(rows["gpu/persistence"], (1, 1, 1))
        self.assertEqual(rows["gpu/power_limit"], (1, 1, 1))
        self.assertEqual(rows["gpu/clock_lock"], (1, 1, 0))
        self.assertIn("nvidia-persistenced keeps it", err)
        self.assertNotIn("-pm", " ".join(self.calls()))

    def test_check_lists_and_sets_nothing(self):
        self.rows(self.card("0", ag="1500"))
        out = io.StringIO()
        policy.run_gpus(self.gpu, fix=False, out=out)
        self.assertIn("GPU 0 gpu/application_clocks: graphics 1500 MHz, wants 2490 MHz", out.getvalue())
        self.assertEqual(self.calls(), [])

    def test_no_nvidia_smi_no_gpu_settings(self):
        self.assertEqual(
            policy.run_gpus({"nvidia_smi": "no-such-nvidia-smi", "power_cap_file": ""}, fix=True), []
        )


if __name__ == "__main__":
    unittest.main()
