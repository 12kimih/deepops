"""cluster_tools.io_fairness: which disk a mount is set on, the lines the kernel is given,
and what a run reports. sysfs and the cgroup root are stand-in directories."""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
from cluster_tools import config  # noqa: E402
from cluster_tools import io_fairness as iocost  # noqa: E402

MODEL = {"rbps": 1272070703, "rseqiops": 60866, "rrandiops": 1587, "wbps": 894575285, "wseqiops": 565,
         "wrandiops": 483}  # fmt: skip
QOS = {"rpct": 95, "rlat": 200278, "wpct": 95, "wlat": 400556, "min": 50, "max": 150}


class Tree:
    """A sysfs with sda (whole), sdb with partition sdb1, dm-0 and md0, and a cgroup root."""

    def __init__(self, d):
        self.sys, self.cg = Path(d, "sys"), Path(d, "cgroup")
        self.cg.mkdir()
        for name in ("io.cost.model", "io.cost.qos"):
            (self.cg / name).write_text("")
        devices = {"sda": ("8:0", None), "sdb": ("8:16", None), "sdb/sdb1": ("8:17", "partition"),
                   "dm-0": ("253:0", "dm"), "md0": ("9:0", "md")}  # fmt: skip
        for path, (dev, kind) in devices.items():
            node = self.sys / "block" / path
            node.mkdir(parents=True)
            (node / "dev").write_text(dev + "\n")
            if kind == "partition":
                (node / "partition").write_text("1\n")
            elif kind:
                (node / kind).mkdir()
            if "/" not in path and kind is None:
                (node / "queue").mkdir()
            link = self.sys / "dev/block" / dev
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(node)

    def on(self, major, minor):
        """Every path on MAJOR:MINOR."""
        return mock.patch.object(iocost, "device_number", lambda path: os.makedev(major, minor))


def cfg(tree, d, enable=False):
    return {"cgroup_root": str(tree.cg), "sys_root": str(tree.sys), "textfile": f"{d}/prom/io.prom",
            "devices": [{"mount": "/home", "enable": enable, "model": MODEL, "qos": QOS}]}  # fmt: skip


class Disk(unittest.TestCase):
    def test_a_partition_is_set_on_its_whole_disk(self):
        with tempfile.TemporaryDirectory() as d:
            tree = Tree(d)
            with tree.on(8, 17):
                self.assertEqual(iocost.disk_of("/home", str(tree.sys)), "8:16")
            with tree.on(8, 0):
                self.assertEqual(iocost.disk_of("/", str(tree.sys)), "8:0")

    def test_stacked_and_blockless_filesystems_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            tree = Tree(d)
            for (major, minor), why in (
                ((253, 0), "dm device"),
                ((9, 0), "md device"),
                ((0, 52), "no block device"),
            ):
                with (
                    self.subTest(why=why),
                    tree.on(major, minor),
                    self.assertRaises(iocost.Unusable) as caught,
                ):
                    iocost.disk_of("/x", str(tree.sys))
                self.assertIn(why, str(caught.exception))


class Apply(unittest.TestCase):
    def test_the_measured_model_then_the_qos_off_until_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            tree = Tree(d)
            with tree.on(8, 17):
                self.assertEqual(iocost.apply(cfg(tree, d), out=io.StringIO()), 0)
            self.assertEqual(
                (tree.cg / "io.cost.model").read_text(),
                "8:16 ctrl=user model=linear rbps=1272070703 rseqiops=60866 rrandiops=1587 "
                "wbps=894575285 wseqiops=565 wrandiops=483\n",
            )
            self.assertEqual(
                (tree.cg / "io.cost.qos").read_text(),
                "8:16 enable=0 ctrl=user rpct=95 rlat=200278 wpct=95 wlat=400556 min=50 max=150\n",
            )
            self.assertIn(
                'io_fairness_enabled{mount="/home",device="8:16"} 0', Path(d, "prom/io.prom").read_text()
            )

    def test_enabled_and_reset(self):
        with tempfile.TemporaryDirectory() as d:
            tree, devnull = Tree(d), io.StringIO()
            with tree.on(8, 17):
                iocost.apply(cfg(tree, d, enable=True), out=devnull)
                self.assertTrue((tree.cg / "io.cost.qos").read_text().startswith("8:16 enable=1 ctrl=user "))
                self.assertIn("} 1", Path(d, "prom/io.prom").read_text())
                iocost.apply(cfg(tree, d, enable=True), reset=True, out=devnull)
            self.assertEqual((tree.cg / "io.cost.model").read_text(), "8:16 ctrl=auto\n")
            self.assertEqual((tree.cg / "io.cost.qos").read_text(), "8:16 enable=0\n")
            self.assertIn("} 0", Path(d, "prom/io.prom").read_text())

    def test_a_kernel_that_keeps_another_value_fails_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            tree = Tree(d)
            with tree.on(8, 17), mock.patch.object(iocost, "held", lambda *a: "8:16 enable=0 ctrl=auto"):
                self.assertEqual(iocost.apply(cfg(tree, d, enable=True), out=io.StringIO()), 1)

    def test_an_unusable_mount_fails_the_run_and_leaves_the_kernel_alone(self):
        with tempfile.TemporaryDirectory() as d:
            tree = Tree(d)
            with tree.on(253, 0):
                self.assertEqual(iocost.apply(cfg(tree, d), out=io.StringIO()), 1)
            self.assertEqual((tree.cg / "io.cost.model").read_text(), "")


class Config(unittest.TestCase):
    def load(self, **device):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "c.json")
            dev = {"mount": "/home", "enable": False, "model": MODEL, "qos": QOS, **device}
            path.write_text(
                json.dumps({"cgroup_root": "/c", "sys_root": "/s", "textfile": "/t", "devices": [dev]})
            )
            return iocost.load_config(path)

    def test_a_complete_entry(self):
        self.assertEqual(self.load()["devices"][0]["mount"], "/home")

    def test_mistakes_are_named(self):
        for device, why in (
            ({"model": {**MODEL, "rbps": 0}}, "rbps"),
            ({"model": {k: v for k, v in MODEL.items() if k != "wbps"}}, "model needs"),
            ({"qos": {**QOS, "min": 200}}, "min at most max"),
            ({"enable": "yes"}, "enable"),
            ({"mount": "home"}, "absolute"),
        ):
            with self.subTest(why=why), self.assertRaises(config.ConfigError) as caught:
                self.load(**device)
            self.assertIn(why, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
