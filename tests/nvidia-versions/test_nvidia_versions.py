"""plugins/filter/nvidia.py and the table in roles/nvidia_versions."""

import importlib.util
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("nvidia", ROOT / "plugins" / "filter" / "nvidia.py")
nv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nv)
TABLE = yaml.safe_load((ROOT / "roles/nvidia_versions/defaults/main.yml").read_text())[
    "nvidia_cuda_release_driver_branch"
]


class CudaDriver(unittest.TestCase):
    def test_each_major_runs_on_its_first_release_branch(self):
        self.assertEqual(nv.cuda_min_driver_branch(TABLE), {11: 450, 12: 525, 13: 580})

    def test_a_branch_ships_its_newest_release(self):
        self.assertEqual(nv.cuda_release_for_driver(TABLE, "580"), "13.0")
        self.assertEqual(nv.cuda_release_for_driver(TABLE, 595), "13.2")
        self.assertEqual(nv.cuda_release_for_driver(TABLE, "585"), "13.0")

    def test_ordering_is_numeric(self):
        self.assertEqual(nv.cuda_release_for_driver({"12.9": 575, "12.10": 576}, 576), "12.10")

    def test_a_branch_older_than_every_release_is_refused(self):
        with self.assertRaises(ValueError):
            nv.cuda_release_for_driver(TABLE, 400)


if __name__ == "__main__":
    unittest.main()
