"""plugins/module_utils/deepops_marks.py: what shows deepops made a file."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("deepops_marks", ROOT / "plugins/module_utils/deepops_marks.py")
marks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(marks)


class Marks(unittest.TestCase):
    def test_text_name_and_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name, text in [
                ("a.conf", "# Ansible managed\nx = 1\n"),
                ("b.conf", "# roles/rsyslog_client: forward\n"),
                ("c.conf", "# written by the site\n"),
                ("deepops-d.conf", "plain\n"),
            ]:
                (base / name).write_text(text)
            got = {p.name: marks.made_by_deepops(str(p)) for p in sorted(base.iterdir())}
            self.assertEqual(got, {"a.conf": True, "b.conf": True, "c.conf": False, "deepops-d.conf": True})
            self.assertFalse(marks.made_by_deepops(str(base / "missing")), "nothing there, nothing ours")
            self.assertTrue(marks.named_deepops("/var/cache/deepops/package-repos/x.key"))
            self.assertFalse(marks.named_deepops("/etc/prometheus/prometheus.yml"))


if __name__ == "__main__":
    unittest.main()
