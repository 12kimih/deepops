"""roles/prometheus's scrape of the exporters: node_exporter's mountstats keeps its
per-mount counters with the mountpoint the host names, and drops the rest.

node_exporter reads its own process's mount table, and its container sees the host's
mounts under node_exporter_rootfs (/host/home for /home), so the scrape strips that.
Prometheus joins the source labels with ";" and anchors the regex at both ends; the
rules are applied here that way, on the rendered file."""

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import render, role_defaults  # noqa: E402

TEMPLATE = (ROOT / "roles/prometheus/templates/prometheus.yml.j2").read_text()


def scrape_job():
    values = {**role_defaults("prometheus_paths"), **role_defaults("prometheus"), "ansible_managed": "",
              "inventory_hostname": "m", "prometheus_slurmctld_targets": []}  # fmt: skip
    cfg = yaml.safe_load(render(TEMPLATE, values))
    return next(job for job in cfg["scrape_configs"] if job["job_name"] == "cluster")


def relabel(rules, labels):
    """LABELS after RULES (keep the action subset these rules use: drop, replace)."""
    labels = dict(labels)
    for rule in rules:
        value = ";".join(labels.get(name, "") for name in rule["source_labels"])
        match = re.fullmatch(rule["regex"], value)
        action = rule.get("action", "replace")
        if action == "drop" and match:
            return None
        if action == "replace" and match:
            labels[rule["target_label"]] = match.expand(rule.get("replacement", "$1").replace("$", "\\"))
    return labels


class Mountstats(unittest.TestCase):
    def setUp(self):
        self.rules = scrape_job()["metric_relabel_configs"]

    def test_the_mountpoint_is_the_hosts(self):
        out = relabel(
            self.rules, {"__name__": "node_mountstats_nfs_mountpoint_info", "mountpoint": "/host/home"}
        )
        self.assertEqual(out["mountpoint"], "/home")
        nested = relabel(
            self.rules, {"__name__": "node_mountstats_nfs_mountpoint_info", "mountpoint": "/host/a/b"}
        )
        self.assertEqual(nested["mountpoint"], "/a/b")

    def test_only_the_per_mount_counters_stay(self):
        for name, kept in (("node_mountstats_nfs_total_read_bytes_total", True),
                           ("node_mountstats_nfs_operations_requests_total", False),
                           ("node_mountstats_nfs_transport_sends_total", False),
                           ("node_mountstats_nfs_event_vfs_open_total", False),
                           ("node_load1", True)):  # fmt: skip
            with self.subTest(name=name):
                self.assertEqual(relabel(self.rules, {"__name__": name}) is not None, kept)

    def test_other_metrics_keep_their_mountpoint(self):
        out = relabel(self.rules, {"__name__": "node_filesystem_avail_bytes", "mountpoint": "/host/home"})
        self.assertEqual(out["mountpoint"], "/host/home")


if __name__ == "__main__":
    unittest.main()
