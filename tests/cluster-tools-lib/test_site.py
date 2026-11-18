"""Tests for clustertools.site and its agreement with the role that writes the file."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
sys.path.insert(0, str(ROOT / "tests/lib"))
from ansible_yaml import load  # noqa: E402
from clustertools import site  # noqa: E402


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
        self.assertEqual(defaults["cluster_tools_lib_info_file"], site.PATH)
        self.assertEqual(
            set(defaults["cluster_info"]), {"name", "docs_url", "dashboard_url", "login_host", "ssh_port"}
        )


if __name__ == "__main__":
    unittest.main()
