"""plugins/module_utils/deepops_marks.py: what shows deepops made a file."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

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
            (base / "e.conf").write_text("# DeepOps: forward\n")
            # The audit counts any mark; a removal needs deepops' word or the role's own.
            got = {p.name: marks.text_marked(str(p)) for p in sorted(base.iterdir())}
            self.assertEqual(
                got,
                {"a.conf": True, "b.conf": True, "c.conf": False, "deepops-d.conf": False, "e.conf": True},
            )
            got = {p.name: marks.made_by(str(p), "rsyslog_client") for p in sorted(base.iterdir())}
            self.assertEqual(
                got,
                {"a.conf": False, "b.conf": True, "c.conf": False, "deepops-d.conf": True, "e.conf": True},
            )
            self.assertFalse(marks.made_by(str(base / "b.conf"), "rsyslog_server"), "another role's file")
            self.assertFalse(
                marks.made_by(str(base / "missing"), "rsyslog_client"), "nothing there, nothing ours"
            )
            self.assertTrue(marks.named_deepops("/var/cache/deepops/package-repos/x.key"))
            self.assertFalse(marks.named_deepops("/etc/prometheus/prometheus.yml"))

    def test_a_role_mark_names_that_role_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name, text in [
                ("own", "# Managed by roles/node_user_policy: only these users\n"),
                ("dot", "// Managed by roles/node_user_policy. Only root\n"),
                ("generic", "# Ansible managed\n"),
                ("longer", "# roles/node_user_policy_extra\n"),
                ("dashed", "# roles/node_user_policy-extra\n"),
            ]:
                (base / name).write_text(text)
            got = {p.name: marks.role_marked(str(p), "node_user_policy") for p in sorted(base.iterdir())}
            self.assertEqual(
                got, {"dashed": False, "dot": True, "generic": False, "longer": False, "own": True}
            )
            self.assertFalse(marks.role_marked(str(base / "missing"), "node_user_policy"))

    def test_only_the_audit_counts_the_generic_header(self):
        # text_marked trusts "Ansible managed", a site's own header too: a module that
        # removes on it would take a site's file (marked_file did, until made_by).
        users = sorted(
            str(p.relative_to(ROOT))
            for p in tracked("roles/*.py", "plugins/*.py")
            if p.name != "deepops_marks.py" and "text_marked" in p.read_text(errors="replace")
        )
        self.assertEqual(users, ["roles/stale_files/library/stale_files.py"])


if __name__ == "__main__":
    unittest.main()
