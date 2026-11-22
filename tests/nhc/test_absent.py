"""Where NHC is absent, what a `make install` from before roles/source_build left goes
too, by NHC's own names; and the files the role writes say they are its own.

A controller that never runs slurmd kept /usr/sbin/nhc, /etc/nhc and /etc/sysconfig/nhc
(pointing at Slurm commands long gone): no manifest listed them, and their "Ansible
managed" header is no evidence of deepops, so only deepops.nhc was removed.
"""

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402
from ansible_yaml import render, role_defaults  # noqa: E402

stale_files = in_tree.load("roles/stale_files/library/stale_files.py")
marks = sys.modules["ansible.module_utils.deepops_marks"]
ROLE = ROOT / "roles" / "nhc"


class Absent(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def rules(self):
        variables = {
            **role_defaults("nhc"),
            "nhc_install_dir": str(self.tmp / "usr"),
            "nhc_libexec_dir": str(self.tmp / "usr/libexec"),
            "nhc_config_dir": str(self.tmp / "etc"),
            "nhc_sysconfig_dir": str(self.tmp / "etc/sysconfig"),
        }
        text = (ROLE / "vars" / "main.yml").read_text()
        return yaml.safe_load(render(text, variables))["nhc_stale_files_rules"]

    def test_the_old_install_goes_by_its_own_names(self):
        old = [
            "usr/sbin/nhc",
            "usr/sbin/nhc-genconf",
            "usr/sbin/nhc-wrapper",
            "usr/libexec/nhc/node-mark-offline",
            "etc/nhc/nhc.conf",
            "etc/nhc/scripts/lbnl_hw.nhc",
            "etc/logrotate.d/nhc",
            "etc/sysconfig/nhc",
        ]
        others = ["usr/sbin/nhcx", "usr/libexec/other/nhc", "etc/sysconfig/nhc-other", "etc/logrotate.d/apt"]
        for rel in old + others:
            (self.tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.tmp / rel).write_text("x\n")
        found = set()
        for rule in self.rules():
            found |= set(stale_files.match_rule(rule))
        tops = {
            str(self.tmp / p)
            for p in (
                "usr/sbin/nhc",
                "usr/sbin/nhc-genconf",
                "usr/sbin/nhc-wrapper",
                "usr/libexec/nhc",
                "etc/nhc",
                "etc/logrotate.d/nhc",
                "etc/sysconfig/nhc",
            )
        }
        self.assertEqual(found, tops)

    def test_absent_applies_the_rules(self):
        tasks = yaml.safe_load((ROLE / "tasks" / "absent.yml").read_text())
        stale = [
            t for t in tasks if (t.get("ansible.builtin.include_role") or {}).get("name") == "stale_files"
        ]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["vars"]["stale_files_rules"], "{{ nhc_stale_files_absent }}")
        facts = [t for t in tasks if (t.get("ansible.builtin.set_fact") or {}).get("nhc_stale_files_absent")]
        self.assertEqual(
            facts[0]["ansible.builtin.set_fact"]["nhc_stale_files_absent"], "{{ nhc_stale_files_rules }}"
        )


class Marks(unittest.TestCase):
    def test_what_the_role_writes_names_it(self):
        variables = {
            **role_defaults("nhc"),
            **role_defaults("slurm_paths"),
            "ansible_managed": "Ansible managed",
            "slurm_install_prefix": "/usr",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "nhc")
            text = render((ROLE / "templates" / "sysconfig_nhc.j2").read_text(), variables)
            path.write_text(text)
            self.assertTrue(marks.made_by(str(path), "nhc"))
        self.assertIn("PATH=/sbin:/usr/sbin:/bin:/usr/bin\n", text)
        other = render(
            (ROLE / "templates" / "sysconfig_nhc.j2").read_text(),
            {**variables, "slurm_install_prefix": "/opt/slurm"},
        )
        self.assertIn("PATH=/sbin:/usr/sbin:/bin:/usr/bin:/opt/slurm/bin\n", other)
        self.assertTrue(
            (ROLE / "templates" / "nhc.conf.j2").read_text().startswith("# Managed by roles/nhc ")
        )


if __name__ == "__main__":
    unittest.main()
