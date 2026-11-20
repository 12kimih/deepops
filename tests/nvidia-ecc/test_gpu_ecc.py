"""gpu_ecc_plan (plugins/filter/gpu_ecc.py) on nvidia-smi's ECC report.

A node held GPUs in both modes (three with ECC off, five on), so the role must look at
each GPU, set only the ones whose pending mode differs and ask for a reboot while any
still runs the other mode.
"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("gpu_ecc", ROOT / "plugins" / "filter" / "gpu_ecc.py")
gpu_ecc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gpu_ecc)

MIXED = """\
0, Disabled, Disabled
1, Enabled, Enabled
2, Enabled, Disabled
3, [N/A], [N/A]
"""


class Plan(unittest.TestCase):
    def test_mixed_node(self):
        self.assertEqual(
            gpu_ecc.gpu_ecc_plan(MIXED, "disabled"), {"set": [1], "reboot": [1, 2], "unsupported": [3]}
        )
        self.assertEqual(
            gpu_ecc.gpu_ecc_plan(MIXED, "Enabled"), {"set": [0, 2], "reboot": [0], "unsupported": [3]}
        )

    def test_converged_and_empty(self):
        done = "0, Disabled, Disabled\n1, Disabled, Disabled\n"
        self.assertEqual(gpu_ecc.gpu_ecc_plan(done, "disabled"), {"set": [], "reboot": [], "unsupported": []})
        self.assertEqual(gpu_ecc.gpu_ecc_plan("", "disabled"), {"set": [], "reboot": [], "unsupported": []})

    def test_unknown_mode_refused(self):
        with self.assertRaises(ValueError):
            gpu_ecc.gpu_ecc_plan(MIXED, "off")


if __name__ == "__main__":
    unittest.main()
