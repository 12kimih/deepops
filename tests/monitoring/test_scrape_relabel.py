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
    """LABELS after RULES (the action subset these rules use: drop, replace, labeldrop)."""
    labels = dict(labels)
    for rule in rules:
        value = ";".join(labels.get(name, "") for name in rule.get("source_labels", []))
        match = re.fullmatch(rule["regex"], value)
        action = rule.get("action", "replace")
        if action == "drop" and match:
            return None
        if action == "labeldrop":
            labels = {k: v for k, v in labels.items() if not re.fullmatch(rule["regex"], k)}
            continue
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

    def test_only_the_per_mount_counters_and_the_named_operations_latency_stay(self):
        ops = role_defaults("prometheus")["prometheus_nfs_client_operations"]
        self.assertIn("WRITE", ops)
        for name, op, kept in (("node_mountstats_nfs_total_read_bytes_total", "", True),
                               ("node_mountstats_nfs_operations_requests_total", "WRITE", True),
                               ("node_mountstats_nfs_operations_response_time_seconds_total", "READ", True),
                               ("node_mountstats_nfs_operations_queue_time_seconds_total", "GETATTR", True),
                               ("node_mountstats_nfs_operations_major_timeouts_total", "COMMIT", True),
                               ("node_mountstats_nfs_operations_requests_total", "READDIRPLUS", False),
                               ("node_mountstats_nfs_operations_sent_bytes_total", "WRITE", False),
                               ("node_mountstats_nfs_operations_requests_total", "", False),
                               ("node_mountstats_nfs_transport_sends_total", "", False),
                               ("node_mountstats_nfs_event_vfs_open_total", "", False),
                               ("node_load1", "", True)):  # fmt: skip
            with self.subTest(name=name, operation=op):
                labels = {"__name__": name, **({"operation": op} if op else {})}
                out = relabel(self.rules, labels)
                self.assertEqual(out is not None, kept)
                if out:
                    self.assertNotIn("__tmp_nfs_keep", out, "the mark goes")

    def test_other_metrics_keep_their_mountpoint(self):
        out = relabel(self.rules, {"__name__": "node_filesystem_avail_bytes", "mountpoint": "/host/home"})
        self.assertEqual(out["mountpoint"], "/host/home")


if __name__ == "__main__":
    unittest.main()
