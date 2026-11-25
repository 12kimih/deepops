"""Tests for cluster_tools.site and its agreement with the role that writes the file."""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
sys.path.insert(0, str(ROOT / "tests/lib"))
from ansible_yaml import load  # noqa: E402
from cluster_tools import site  # noqa: E402


class SiteTests(unittest.TestCase):
    def test_reads_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cluster.json"
            path.write_text(json.dumps({"docs_url": "https://docs.example/", "ssh_port": 2222}))
            self.assertEqual(site.info(str(path)), {"docs_url": "https://docs.example/", "ssh_port": 2222})

    def test_absent_or_broken_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(site.info(str(Path(d) / "none.json")), {})
            broken = Path(d) / "broken.json"
            broken.write_text("{not json")
            self.assertEqual(site.info(str(broken)), {})

    def test_callers_cannot_change_the_cache(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.json"
            path.write_text('{"name": "x"}')
            site.info(str(path))["name"] = "changed"
            self.assertEqual(site.info(str(path))["name"], "x")

    def test_the_role_writes_where_the_module_reads(self):
        defaults = load(ROOT / "roles/cluster_tools_lib/defaults/main.yml")
        info_file = defaults["cluster_tools_lib_info_file"].replace(
            "{{ cluster_tools_lib_config_dir }}", defaults["cluster_tools_lib_config_dir"]
        )
        self.assertEqual(info_file, site.PATH)
        self.assertEqual(
            set(load(ROOT / "roles/cluster_info_defaults/defaults/main.yml")["cluster_info"]),
            {"name", "docs_url", "login_host", "ssh_port"},
        )
        self.assertEqual(
            set(defaults["cluster_tools_lib_derived_info"]),
            {"dashboard_url", "prometheus_url", "gpu_allocation_interval"},
        )

    def test_every_fact_a_program_reads_is_published(self):
        # A key read but published by neither dict is always missing on a host.
        defaults = load(ROOT / "roles/cluster_tools_lib/defaults/main.yml")
        published = set(load(ROOT / "roles/cluster_info_defaults/defaults/main.yml")["cluster_info"]) | set(
            defaults["cluster_tools_lib_derived_info"]
        )
        package = ROOT / "roles/cluster_tools_lib/files/cluster_tools"
        read = {
            key
            for path in package.glob("*.py")
            for key in re.findall(r"""site_info\(\)(?:\.get\(|\[)["'](\w+)""", path.read_text())
        }
        self.assertTrue(read, "no reader found: the pattern is stale")
        self.assertLessEqual(read, published)


if __name__ == "__main__":
    unittest.main()
