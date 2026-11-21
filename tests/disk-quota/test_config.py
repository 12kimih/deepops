"""roles/disk_quota writes clustertools.disk_quota's configuration from the roster: every
active person limited (the site's limits under their own), the rest of the roster
released, nobody outside it named."""

import json
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import render, role_defaults  # noqa: E402

VARS = yaml.safe_load((ROOT / "roles/disk_quota/vars/main.yml").read_text())
PEOPLE = yaml.safe_load((ROOT / "config.example/people.yml").read_text())["people"]


def config(**overrides):
    values = {**role_defaults("disk_quota"), **VARS, "people": PEOPLE, **overrides}
    return json.loads(render("{{ _disk_quota_cfg | to_json }}", values))


class Config(unittest.TestCase):
    def test_counting_only_limits_who_has_their_own(self):
        cfg = config()
        self.assertEqual(cfg["filesystems"], ["/"])
        self.assertEqual(list(cfg["limits"]), ["alice"])
        self.assertEqual(cfg["release"], ["bob", "carol", "dave"])
        self.assertEqual(cfg["grace_days"], 14, "the site's one cleanup period, cluster_user_grace_days")
        self.assertTrue(cfg["textfile"].endswith("/disk-quota.prom"))

    def test_site_limits_reach_every_active_person(self):
        cfg = config(disk_quota_limits={"block_soft": "10G", "block_hard": "20G"})
        self.assertEqual(sorted(cfg["limits"]), ["alice", "bob", "dave"])
        self.assertEqual(cfg["limits"]["alice"]["block_hard"], "100G")
        self.assertEqual(cfg["release"], ["carol"])

    def test_no_roster_names_nobody(self):
        cfg = config(people={}, disk_quota_limits={"block_hard": "20G"})
        self.assertEqual((cfg["limits"], cfg["release"]), ({}, []))


if __name__ == "__main__":
    unittest.main()
