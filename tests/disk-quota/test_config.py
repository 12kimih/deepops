"""roles/disk_quota writes cluster_tools.disk_quota's configuration from the roster: every
active person limited on each filesystem (the site's limits there under their own), the
whole roster named for release where it is not limited, nobody outside it named."""

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
    values = {
        **role_defaults("disk_quota"),
        **VARS,
        "people": PEOPLE,
        "ansible_facts": {"mounts": [{"mount": "/"}]},
        **overrides,
    }
    return json.loads(render("{{ _disk_quota_cfg | to_json }}", values))


class Config(unittest.TestCase):
    def test_counting_only_limits_who_has_their_own(self):
        cfg = config()
        self.assertEqual(list(cfg["filesystems"]), ["/"])
        self.assertEqual(list(cfg["filesystems"]["/"]), ["alice"])
        self.assertEqual(cfg["release"], ["alice", "bob", "carol", "dave"])
        self.assertNotIn("grace_days", cfg, "a warn level only notifies: no grace period to set")
        self.assertEqual(cfg["projects"], {})
        self.assertTrue(cfg["textfile"].endswith("/disk-quota.prom"))
        self.assertEqual(
            (cfg["warn_fraction"], cfg["inodes_per_tib"]), (0.9, 0), "the cluster's one fraction"
        )

    def test_site_limits_reach_every_active_person(self):
        cfg = config(disk_quota_filesystems={"/": {"block_hard": "20G"}})
        self.assertEqual(sorted(cfg["filesystems"]["/"]), ["alice", "bob", "dave"])
        self.assertEqual(cfg["filesystems"]["/"]["alice"]["block_hard"], "100G")

    def test_each_filesystem_has_its_own_limits(self):
        cfg = config(disk_quota_filesystems={"/": {}, "/scratch": {"block_hard": "5T"}})
        self.assertEqual(list(cfg["filesystems"]["/"]), ["alice"], "alice's own limit is for / alone")
        self.assertEqual(sorted(cfg["filesystems"]["/scratch"]), ["alice", "bob", "dave"])
        self.assertEqual(cfg["filesystems"]["/scratch"]["alice"], {"block_hard": "5T"})

    def test_an_announced_limit_carries_its_day_as_unix_time(self):
        """{block_hard, from}: the day reaches the tool as its local midnight, quoted or not."""
        import datetime
        import time

        midnight = int(time.mktime(datetime.date(2026, 12, 9).timetuple()))
        for day in ("2026-12-09", datetime.date(2026, 12, 9)):
            with self.subTest(day=day):
                cfg = config(disk_quota_filesystems={"/": {"block_hard": "10T", "from": day}})
                self.assertEqual(cfg["filesystems"]["/"]["bob"], {"block_hard": "10T", "from": midnight})
        with self.assertRaisesRegex(Exception, "from is a day"):
            config(disk_quota_filesystems={"/": {"block_hard": "10T", "from": "next week"}})

    def test_the_old_list_is_refused_by_name(self):
        with self.assertRaisesRegex(Exception, "disk_quota_filesystems is filesystem"):
            config(disk_quota_filesystems=["/"])

    def test_each_project_carries_the_filesystem_it_lives_on(self):
        mounts = [{"mount": "/"}, {"mount": "/home"}]
        cfg = config(
            disk_quota_projects={"/home/shared/datasets": {"id": 101, "block_hard": "20T"}},
            ansible_facts={"mounts": mounts},
        )
        self.assertEqual(
            cfg["projects"],
            {"/home/shared/datasets": {"id": 101, "block_hard": "20T", "filesystem": "/home"}},
        )

    def test_no_roster_names_nobody(self):
        cfg = config(people={}, disk_quota_filesystems={"/": {"block_hard": "20G"}})
        self.assertEqual((cfg["filesystems"], cfg["release"]), ({"/": {}}, []))


if __name__ == "__main__":
    unittest.main()
