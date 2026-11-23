"""scripts/docs/site_facts.py: the docs' facts from code and config, and the public file
holding only what PUBLIC allows (the user docs site is published)."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("site_facts", ROOT / "scripts/docs/site_facts.py")
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)


def site(tmp, all_yml="", host_vars=None):
    config = Path(tmp) / "config"
    (config / "group_vars").mkdir(parents=True)
    (config / "host_vars").mkdir()
    (config / "group_vars" / "all.yml").write_text(all_yml)
    for host, text in (host_vars or {}).items():
        (config / "host_vars" / host).write_text(text)
    return config


class Facts(unittest.TestCase):
    def test_commands_come_from_their_one_definition(self):
        got = sf.commands(["sq"])["sq"]
        flags = [f for o in got["options"] for f in o["flags"]]
        self.assertTrue(got["summary"] and got["usage"].startswith("sq"))
        self.assertIn("-u", flags)

    def test_the_site_overrides_a_default_and_templates_and_vaulted_values_stay_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = site(tmp, "cluster_user_stale_days: 30\nslack_admin_group: \"{{ x }}\"\nsecret: !vault |\n  abc\n",
                          {"node1": "disk_quota_filesystems: {/data: {block_hard: 1T}}\n"})  # fmt: skip
            every = sf.facts(config)
        self.assertEqual(every["policy"]["stale_days"], 30)
        self.assertEqual(every["policy"]["grace_days"], 14, "the role default where the site sets none")
        self.assertIsNone(every["slack"]["admin_group"], "a template is Ansible's to resolve")
        self.assertEqual(
            every["quotas"]["hosts"], {"node1": {"filesystems": {"/data": {"block_hard": "1T"}}}}
        )


class PublicAllowList(unittest.TestCase):
    def test_only_allowed_keys_and_fields_reach_the_public_file(self):
        every = {
            "commands": {"sq": {"summary": "s", "usage": "sq", "options": [], "internal": 1}},
            "policy": {"stale_days": 60, "grace_days": 14, "warn_fraction": 0.9, "extra": 1},
            "slack": {"channels": {"admin": "#x"}},
            "quotas": {"hosts": {"n": {}}},
            "anything_new": {"a": 1},
        }
        out = sf.public(every)
        self.assertEqual(set(out), set(sf.PUBLIC))
        self.assertEqual(set(out["commands"]["sq"]), {"summary", "usage", "options"})
        self.assertEqual(set(out["policy"]), {"stale_days", "grace_days", "warn_fraction"})
        self.assertNotIn("slack", json.dumps(out))

    def test_the_allow_list_holds_nothing_private(self):
        self.assertFalse({"slack", "quotas"} & set(sf.PUBLIC), "channels and limits stay in the private file")


class Check(unittest.TestCase):
    def test_check_names_a_stale_file_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = site(tmp)
            out = Path(tmp) / "pub.json"
            self.assertEqual(sf.main(["--config", str(config), "--public", str(out)]), 0)
            self.assertEqual(sf.main(["--config", str(config), "--public", str(out), "--check"]), 0)
            out.write_text("{}\n")
            self.assertEqual(sf.main(["--config", str(config), "--public", str(out), "--check"]), 1)
            self.assertEqual(out.read_text(), "{}\n")


if __name__ == "__main__":
    unittest.main()
