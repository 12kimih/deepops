"""cluster_bot.onboarding: what a newcomer needs before the first login, rendered from the
cluster's settings only (cluster_bot_connect), in the join reply and the account notice."""

import re
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_bot/files"))
from cluster_bot import onboarding  # noqa: E402

CONNECT = {"login_host": "login.example.org", "ssh_port": 2222, "portal_url": "https://portal.example.org/",
           "docs_url": "https://docs.example.org/"}  # fmt: skip
SOURCE = ROOT / "roles/cluster_bot/files/cluster_bot/onboarding.py"


def connect_setting(**site):
    """cluster_bot_connect as the role default renders it with SITE's settings."""
    env = Environment()  # noqa: S701 (not HTML)
    portal = yaml.safe_load((ROOT / "roles/open_ondemand_defaults/defaults/main.yml").read_text())
    info = yaml.safe_load((ROOT / "roles/cluster_info_defaults/defaults/main.yml").read_text())
    site = {**info, **portal, **site}
    site["open_ondemand_url"] = env.from_string(portal["open_ondemand_url"]).render(**site).strip()
    expr = yaml.safe_load((ROOT / "roles/cluster_bot/defaults/main.yml").read_text())["cluster_bot_connect"]
    return {k: env.from_string(v).render(**site).strip() for k, v in expr.items()}


class Text(unittest.TestCase):
    def test_every_fact_comes_from_the_settings(self):
        text = onboarding.connect_text(CONNECT, "carl")
        self.assertIn("`ssh -p 2222 carl@login.example.org`", text)
        self.assertIn("https://portal.example.org/", text)
        self.assertIn("https://docs.example.org/", text)

    def test_an_empty_fact_is_left_out(self):
        text = onboarding.connect_text({"login_host": "login.example.org", "ssh_port": 22, "portal_url": ""})
        self.assertNotIn("포털", text)
        self.assertNotIn("안내", text)
        self.assertEqual(onboarding.connect_text({}), "")

    def test_the_join_reply_says_how_an_account_comes_about_then_how_to_log_in(self):
        text = onboarding.join_text(CONNECT, "/cl")
        self.assertIn("`/cl join`", text)
        self.assertLess(text.index("/cl join"), text.index("login.example.org"))

    def test_nothing_is_typed(self):
        code = re.sub(r'(?s)^""".*?"""', "", SOURCE.read_text())
        self.assertIsNone(re.search(r"://|\b\d{2,5}\b|\b[a-z0-9-]+\.[a-z0-9-]+\.[a-z]{2,}\b", code))


class Setting(unittest.TestCase):
    def test_rendered_from_cluster_info_and_the_portal(self):
        got = connect_setting(
            cluster_info={"login_host": "login.example.org", "ssh_port": 2222, "docs_url": "https://d.example.org/"},
            open_ondemand_servername="portal.example.org", open_ondemand_port=8443,
            slurm_install_open_ondemand=True,
        )  # fmt: skip
        self.assertEqual(got["login_host"], "login.example.org")
        self.assertEqual(got["ssh_port"], "2222")
        self.assertEqual(got["portal_url"], "https://portal.example.org:8443/")
        self.assertEqual(got["docs_url"], "https://d.example.org/")

    def test_no_portal_without_open_ondemand(self):
        got = connect_setting(cluster_info={}, open_ondemand_servername="portal.example.org", open_ondemand_port=443,
                              slurm_install_open_ondemand=False)  # fmt: skip
        self.assertEqual(got["portal_url"], "")
        got = connect_setting(cluster_info={}, open_ondemand_servername="portal.example.org", open_ondemand_port=443,
                              slurm_install_open_ondemand=True)  # fmt: skip
        self.assertEqual(got["portal_url"], "https://portal.example.org/", "443 is not written")
        got = connect_setting(cluster_info={}, slurm_install_open_ondemand=True)
        self.assertEqual(got["portal_url"], "", "no name, no portal")


class Welcome(unittest.TestCase):
    def test_once_each_after_the_first_look(self):
        with tempfile.TemporaryDirectory() as tmp:
            seen, posts, accounts = str(Path(tmp) / "w.json"), [], {"alice"}

            def run(members):
                onboarding.welcome_round(members, seen, accounts.__contains__,
                                         lambda who, text: posts.append((who, text)), CONNECT, "/cl")  # fmt: skip

            run({"UA": "alice"})
            self.assertEqual(posts, [], "the first look only records")
            run({"UA": "alice", "UC": "carl"})
            self.assertEqual(posts, [], "carl's account does not exist yet")
            accounts.add("carl")
            run({"UA": "alice", "UC": "carl"})
            self.assertEqual([w for w, _ in posts], ["UC"])
            self.assertIn("carl@login.example.org", posts[0][1])
            run({"UA": "alice", "UC": "carl"})
            self.assertEqual(len(posts), 1, "once")


if __name__ == "__main__":
    unittest.main()
