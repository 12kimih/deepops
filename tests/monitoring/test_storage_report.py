#!/usr/bin/env python3
"""Execute the daily disk report's PromQL (clustertools.storage_report) with promtool:
the disks it reads are exactly the filesystems the quota metrics count (no cluster)."""

import sys
import tempfile
import unittest
from pathlib import Path

import promtool_runner
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
from clustertools import storage_report  # noqa: E402


def labels(**kw):
    return "{" + ",".join(f'{k}="{v}"' for k, v in sorted(kw.items())) + "}"


class StorageQueryTests(unittest.TestCase):
    def test_disks_are_the_counted_filesystems_only(self):
        login, nfs = {"host": "login", "instance": "login:9100"}, {"host": "nfs", "instance": "nfs:9100"}
        series = [
            # Two node_exporter series for one mount (fstype differs) are one disk.
            ("node_filesystem_size_bytes", dict(login, mountpoint="/", device="/dev/sda1", fstype="ext4"), 400),
            ("node_filesystem_size_bytes", dict(login, mountpoint="/", device="rootfs", fstype="rootfs"), 400),
            ("node_filesystem_size_bytes", dict(login, mountpoint="/boot", device="/dev/sda2", fstype="ext4"), 1),
            ("node_filesystem_size_bytes", dict(nfs, mountpoint="/home", device="/dev/sdb1", fstype="ext4"), 1000),
            ("node_filesystem_size_bytes", dict(nfs, mountpoint="/", device="/dev/sda1", fstype="ext4"), 100),
            ("disk_quota_used_bytes", dict(login, filesystem="/", user="alice"), 5),
            ("disk_quota_used_bytes", dict(login, filesystem="/", user="bob"), 6),
            ("disk_quota_project_used_bytes", dict(nfs, filesystem="/home", project="/home/shared/x", project_id="1"), 7),
        ]  # fmt: skip
        test = {
            "rule_files": [],
            "evaluation_interval": "1m",
            "tests": [
                {
                    "interval": "1m",
                    "input_series": [
                        {"series": name + labels(**lab), "values": f"{v}x5"} for name, lab, v in series
                    ],
                    "promql_expr_test": [
                        {
                            "expr": storage_report.disk_query("node_filesystem_size_bytes"),
                            "eval_time": "2m",
                            "exp_samples": [
                                {"labels": labels(**login, mountpoint="/"), "value": 400},
                                {"labels": labels(**nfs, mountpoint="/home"), "value": 1000},
                            ],
                        }
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "t.yml").write_text(yaml.safe_dump(test))
            result = promtool_runner.run(["test", "rules", "t.yml"], temp)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
