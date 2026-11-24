"""cluster_tools.power_policy: which files a setting covers, what a run sets, refuses
and reports. sysfs is a stand-in directory."""

import io
import json
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

    def test_a_setting_the_kernel_keeps_is_refused_and_fails_the_run(self):
        real_open = open

        def refusing(path, mode="r", *args, **kw):
            if mode == "w" and str(path).endswith("cpu1/cpufreq/scaling_governor"):
                raise PermissionError(1, "Operation not permitted")
            return real_open(path, mode, *args, **kw)

        with mock.patch("builtins.open", refusing), mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(policy.apply(self.tree.cfg, out=io.StringIO(), now=1), 1)
        self.assertIn("the kernel keeps powersave (Operation not permitted), not performance", err.getvalue())
        self.assertEqual(self.tree.metrics()[prom.key("power_policy_refused", setting=GOVERNOR)], 1)


if __name__ == "__main__":
    unittest.main()
