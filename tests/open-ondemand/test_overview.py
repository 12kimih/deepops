"""roles/open_ondemand's cluster overview: a portal page behind the login (custom_pages
and a widget) rendered from the private facts, never typed; none without facts."""

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import render  # noqa: E402

TEMPLATES = ROOT / "roles/open_ondemand/templates"
FACTS = {"nodes": [{"name": "n1", "cpus": 8, "memory_mb": 2048, "gres": "gpu:t:2"}],
         "partitions": [{"name": "p", "nodes": "n1", "max_time": "1-00:00:00"}]}  # fmt: skip


def values(overview):
    return {"ansible_managed": "managed", "open_ondemand_nav_groups": ["apps"], "open_ondemand_proxied_services": [],
            "open_ondemand_overview": overview, "open_ondemand_overview_title": "Cluster"}  # fmt: skip


class Overview(unittest.TestCase):
    def test_facts_make_a_page_a_menu_link_and_a_widget(self):
        menu = yaml.safe_load(render((TEMPLATES / "ondemand-menu.yml.j2").read_text(), values(FACTS)))
        self.assertEqual(
            menu["custom_pages"]["cluster"]["rows"][0]["columns"][0]["widgets"], ["cluster_overview"]
        )
        self.assertIn(
            "/pun/sys/dashboard/custom/cluster",
            [i.get("url") for i in menu["nav_bar"] if isinstance(i, dict)],
        )
        widget = render((TEMPLATES / "cluster-overview.html.erb.j2").read_text(), values(FACTS))
        self.assertIn("<td>n1</td><td>8</td><td>2</td><td>gpu:t:2</td>", widget)
        self.assertNotIn("<%=", widget, "no Ruby in the widget")

    def test_no_facts_no_page(self):
        menu = yaml.safe_load(render((TEMPLATES / "ondemand-menu.yml.j2").read_text(), values({})))
        self.assertNotIn("custom_pages", menu)
        self.assertEqual(menu["nav_bar"], ["apps"])


if __name__ == "__main__":
    unittest.main()
