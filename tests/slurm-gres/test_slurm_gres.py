"""slurm_gres_devices (plugins/filter/slurm_gres.py)."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("slurm_gres", ROOT / "plugins" / "filter" / "slurm_gres.py")
gres = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gres)

CONF = """
# n1
NodeName=n1 Name=gpu Type=a File=/dev/nvidia[0-3] Cores=0-47
NodeName=n1 Name=gpu Type=a File=/dev/nvidia[4-7] Cores=48-95
NodeName=n[2-3] Name=gpu Type=b File=/dev/nvidia[0-2,4]
NodeName=n9 AutoDetect=nvml
"""


class GresDevices(unittest.TestCase):
    def test_lines_of_a_node_expand_and_join(self):
        self.assertEqual(gres.slurm_gres_devices(CONF, "n1"), [f"/dev/nvidia{i}" for i in range(8)])

    def test_host_lists_and_gaps(self):
        self.assertEqual(
            gres.slurm_gres_devices(CONF, "n3"),
            ["/dev/nvidia0", "/dev/nvidia1", "/dev/nvidia2", "/dev/nvidia4"],
        )

    def test_a_node_without_lines_has_none(self):
        self.assertEqual(gres.slurm_gres_devices(CONF, "cpu1"), [])

    def test_autodetect_leaves_nothing_to_check(self):
        self.assertIsNone(gres.slurm_gres_devices(CONF, "n9"))
        self.assertIsNone(gres.slurm_gres_devices("AutoDetect=nvml\n", "n1"))
        self.assertEqual(
            gres.slurm_gres_devices(
                "AutoDetect=nvml\nNodeName=n1 AutoDetect=off Name=gpu File=/dev/nvidia0\n", "n1"
            ),
            ["/dev/nvidia0"],
        )


if __name__ == "__main__":
    unittest.main()
