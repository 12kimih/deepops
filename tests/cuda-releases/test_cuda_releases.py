"""scripts/deepops/cuda_releases.py, and the catalog it writes into the role defaults."""

import importlib.util
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cuda_releases", ROOT / "scripts/deepops/cuda_releases.py")
cr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cr)


class Generator(unittest.TestCase):
    def test_newest_patch_of_each_minor(self):
        index = "redistrib_12.6.2.json redistrib_12.6.3.json redistrib_12.8.0.json redistrib_10.2.89.json"
        self.assertEqual(cr.newest_per_minor(cr.releases_in(index)), [(12, 6, 3), (12, 8, 0)])

    def test_runfile_with_and_without_the_driver_version(self):
        text = (
            "aa  cuda_12.8.2_570.211.01_linux_sbsa.run\n"
            "82a348acc1271169ce3d2126f941f65f cuda_12.8.2_570.211.01_linux.run\n"
            "d79b49d3ab39f5d45d48d91b31275de4 cuda_13.4.3_linux.run\n"
        )
        self.assertEqual(
            cr.runfile_in(text, (12, 8, 2)),
            ("cuda_12.8.2_570.211.01_linux.run", "82a348acc1271169ce3d2126f941f65f"),
        )
        self.assertEqual(
            cr.runfile_in(text, (13, 4, 3)), ("cuda_13.4.3_linux.run", "d79b49d3ab39f5d45d48d91b31275de4")
        )
        with self.assertRaises(LookupError):
            cr.runfile_in(text, (13, 5, 0))

    def test_the_block_round_trips(self):
        block = cr.render([((13, 5, 0), "cuda_13.5.0_linux.run", "6c1f56882ef5804043b15ff09e75d630")])
        text = f"a: 1\n{cr.BEGIN}\ncuda_releases: []\n{cr.END}\nb: 2\n"
        out = cr.replace_block(text, block)
        self.assertEqual(cr.current_block(out), block)
        self.assertEqual(
            yaml.safe_load(out)["cuda_releases"][0]["checksum"], "md5:6c1f56882ef5804043b15ff09e75d630"
        )


class Catalog(unittest.TestCase):
    def setUp(self):
        self.defaults = yaml.safe_load(cr.DEFAULTS.read_text())
        self.releases = self.defaults["cuda_releases"]

    def test_one_entry_per_minor_oldest_first(self):
        versions = [tuple(map(int, r["version"].split("."))) for r in self.releases]
        self.assertEqual(versions, sorted(versions))
        self.assertEqual(len({v[:2] for v in versions}), len(versions))

    def test_every_entry_is_pinned_to_its_runfile(self):
        for r in self.releases:
            with self.subTest(version=r["version"]):
                self.assertRegex(r["runfile"], rf"^cuda_{re.escape(r['version'])}(_[0-9.]+)?_linux\.run$")
                self.assertRegex(r["checksum"], r"^md5:[0-9a-f]{32}$")

    def test_the_default_range_starts_inside_the_catalog(self):
        minors = {r["version"].rsplit(".", 1)[0] for r in self.releases}
        self.assertIn(self.defaults["cuda_toolkit_oldest"], minors)


if __name__ == "__main__":
    unittest.main()
