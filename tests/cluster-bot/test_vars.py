"""roles/cluster_bot/vars: the bot's configuration renders from a real roster's shape.
An administrator without a Slack member (a shared admin login) is left out of the
approvers; looking that login up in the members failed the whole configuration on
ansible-core 2.19 and newer, where a missing key is an error, not a value to drop
(ARA 308, 2026-11-25)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from ansible_yaml import load, render  # noqa: E402

VARS = load("roles/cluster_bot/vars/main.yml")


class Admins(unittest.TestCase):
    def admins(self, admins, members):
        return render(
            VARS["_cluster_bot_cfg"]["admins"], {"cluster_bot_admins": admins, "cluster_bot_members": members}
        )

    def test_an_admin_without_a_slack_member_is_left_out(self):
        self.assertEqual(self.admins(["alice", "shared"], {"alice": "U1", "bob": "U2"}), ["U1"])

    def test_no_admin_reachable_is_an_empty_list(self):
        self.assertEqual(self.admins(["shared"], {}), [])


if __name__ == "__main__":
    unittest.main()
