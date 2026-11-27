"""cluster_tools.ssh_rate: the one decision on a new SSH login (roles/ssh_rate). The PAM
side, in a container: tests/ssh-rate/test_pam.py."""

import os
import pwd
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
from cluster_tools import prom, ssh_rate  # noqa: E402

NOW = 1_800_000_000
CFG = {"mode": "refuse", "window_minutes": 10, "warn_sessions": 3, "refuse_sessions": 5, "block_minutes": 10,
       "block_max_minutes": 60, "exempt_users": ["boss"], "state_dir": "", "textfile": "", "docs_url": "https://d/"}  # fmt: skip


def opened(n, at=NOW, step=1.0):
    return {"sessions": [at - n * step + i * step for i in range(n)]}


class Decide(unittest.TestCase):
    def test_below_the_refuse_level_a_login_goes_through(self):
        allowed, data = ssh_rate.decide(opened(4), CFG, NOW)
        self.assertTrue(allowed)
        self.assertEqual(data["blocked_until"], 0)

    def test_at_the_refuse_level_a_block_starts(self):
        allowed, data = ssh_rate.decide(opened(5), CFG, NOW)
        self.assertFalse(allowed)
        self.assertEqual((data["block_start"], data["blocked_until"]), (NOW, NOW + 600))

    def test_sessions_out_of_the_window_do_not_count(self):
        self.assertTrue(ssh_rate.decide(opened(50, at=NOW - 601), CFG, NOW)[0])

    def test_a_person_who_stops_is_free_when_the_block_ends(self):
        _, data = ssh_rate.decide(opened(5), CFG, NOW)
        self.assertFalse(ssh_rate.decide(data, CFG, NOW + 60)[0], "still blocked")
        _, data = ssh_rate.decide(data, CFG, NOW + 60)
        allowed, data = ssh_rate.decide(data, CFG, NOW + 601)
        self.assertTrue(allowed, "the block ended and the old sessions left the window")

    def test_a_loop_while_blocked_extends_the_block_up_to_the_cap(self):
        allowed, data = ssh_rate.decide(opened(5), CFG, NOW)
        t = NOW
        for _ in range(200):  # a client retrying every 10 s for over half an hour
            t += 10
            allowed, data = ssh_rate.decide(data, CFG, t)
            self.assertFalse(allowed)
        self.assertGreater(data["blocked_until"], NOW + 600, "extended")
        self.assertLessEqual(data["blocked_until"], NOW + 3600, "never past block_max_minutes")
        for _ in range(2000):
            t += 10
            if t >= data["blocked_until"]:
                break
            _, data = ssh_rate.decide(data, CFG, t)
        self.assertEqual(data["blocked_until"], NOW + 3600, "capped")

    def test_the_message_names_the_count_the_time_and_controlmaster(self):
        _, data = ssh_rate.decide(opened(5), CFG, NOW)
        text = ssh_rate.message(data, CFG)
        self.assertTrue(text.startswith(ssh_rate.REFUSED))
        for part in ("5번", "한도 5번", "ControlMaster/ControlPersist를 켜세요", "https://d/"):
            self.assertIn(part, text)


class Pam(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cfg = {**CFG, "state_dir": os.path.join(self.dir.name, "state"),
                    "textfile": os.path.join(self.dir.name, "ssh-rate.prom")}  # fmt: skip
        person = pwd.struct_passwd(("kim", "x", 5000, 5000, "", "/home/kim", "/bin/bash"))
        service = pwd.struct_passwd(("svc", "x", 120, 120, "", "/", "/bin/false"))
        accounts = {"kim": person, "svc": service, "boss": person}
        patch = mock.patch.object(ssh_rate.pwd, "getpwnam", side_effect=lambda n: accounts[n])
        patch.start()
        self.addCleanup(patch.stop)

    def login(self, who, now=NOW):
        status = ssh_rate.account(self.cfg, who, now)
        if status == 0:
            ssh_rate.session(self.cfg, who, now)
        return status

    def test_a_person_past_the_level_is_refused_and_the_others_are_not(self):
        self.assertEqual([self.login("kim", NOW + i) for i in range(6)], [0] * 5 + [ssh_rate.REFUSE])
        for who in ("svc", "boss"):
            with self.subTest(exempt=who):
                self.assertEqual({self.login(who, NOW + i) for i in range(20)}, {0})

    def test_warn_mode_counts_and_never_refuses(self):
        self.cfg["mode"] = "warn"
        self.assertEqual({self.login("kim", NOW + i) for i in range(20)}, {0})
        ssh_rate.metrics(self.cfg, NOW + 30)
        values = prom.parse(Path(self.cfg["textfile"]).read_text())
        self.assertEqual(values[prom.key("ssh_rate_new_sessions", user="kim")], "20", "still counted")

    def test_only_sshd_is_counted_or_refused(self):
        config = Path(self.dir.name, "c.json")
        config.write_text(__import__("json").dumps(self.cfg))
        for _ in range(10):
            self.login("kim")
        with mock.patch.dict(os.environ, {"PAM_SERVICE": "sudo", "PAM_USER": "kim"}):
            self.assertEqual(ssh_rate.main([str(config), "account"]), 0)
        with mock.patch.dict(os.environ, {"PAM_SERVICE": "sshd", "PAM_USER": "kim"}):
            self.assertEqual(ssh_rate.main([str(config), "account"]), ssh_rate.REFUSE)

    def test_the_metrics_carry_the_count_and_the_block_and_nothing_else(self):
        for i in range(6):
            self.login("kim", NOW + i)
        self.login("svc", NOW)
        ssh_rate.metrics(self.cfg, NOW + 10)
        values = prom.parse(Path(self.cfg["textfile"]).read_text())
        mine = {name: dict(labels) for (name, labels) in values if dict(labels).get("user") == "kim"}
        self.assertEqual(set(mine), {"ssh_rate_new_sessions", "ssh_rate_blocked_until_seconds",
                                     "ssh_rate_block_start_seconds"})  # fmt: skip
        self.assertEqual({tuple(sorted(v)) for v in mine.values()}, {("user",)}, "no address, no session")
        self.assertEqual(values[prom.key("ssh_rate_new_sessions", user="kim")], "5")
        self.assertNotIn(prom.key("ssh_rate_new_sessions", user="svc"), values, "exempt: never counted")
        ssh_rate.metrics(self.cfg, NOW + 4000)
        self.assertEqual(os.listdir(self.cfg["state_dir"]), [], "nothing left: the file goes")


if __name__ == "__main__":
    unittest.main()
