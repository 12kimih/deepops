"""plugins/filter/arch.py, and that no role keeps its own architecture mapping."""

import importlib.util
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

spec = importlib.util.spec_from_file_location("arch", ROOT / "plugins" / "filter" / "arch.py")
arch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arch)


class Vocabularies(unittest.TestCase):
    def test_each_vocabulary(self):
        self.assertEqual(
            [arch.deb_architecture(m) for m in ("x86_64", "aarch64", "arm64")], ["amd64", "arm64", "arm64"]
        )
        self.assertEqual([arch.rpm_architecture(m) for m in ("x86_64", "arm64")], ["x86_64", "aarch64"])
        self.assertEqual(arch.multiarch_tuple("armv7l"), "arm-linux-gnueabihf")
        self.assertEqual(arch.cuda_architecture("aarch64"), "sbsa")

    def test_an_unknown_architecture_keeps_its_name(self):
        self.assertEqual(arch.deb_architecture("riscv64"), "riscv64")
        self.assertEqual(arch.multiarch_tuple("riscv64"), "riscv64-linux-gnu")


class OneTable(unittest.TestCase):
    # A mapping written in a role: x86_64 beside amd64 (or aarch64 beside arm64/sbsa) in
    # one expression or mapping, instead of the filters.
    PAIR = re.compile(
        r"x86_64['\"]?\s*:\s*['\"]?amd64|aarch64['\"]?\s*:\s*['\"]?arm64|'aarch64'.*'sbsa'|'sbsa'.*'aarch64'"
    )

    def test_no_role_maps_architectures_itself(self):
        files = [p for p in tracked("roles", "playbooks") if p.suffix in (".yml", ".yaml", ".j2")]
        self.assertGreater(len(files), 100)
        offenders = [
            str(p.relative_to(ROOT)) for p in files if self.PAIR.search(p.read_text(errors="replace"))
        ]
        self.assertEqual(offenders, [], "use plugins/filter/arch.py")


if __name__ == "__main__":
    unittest.main()
