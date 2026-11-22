"""clusterbot.bans through the request engine: an unban is asked only for an address
banned now, always waits for an administrator, is lifted on every host, and a host that
cannot be reached is said, never hidden. The service names the bot asks for are the ones
the roles offer, and the request reaches the host as words, never through a shell."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_bot/files"))
from clusterbot import bans, remote, requests, scratch  # noqa: E402

BAN = {"ip": "192.0.2.7", "jail": "sshd", "since": "2026-11-20 03:12:44"}


class Host:
    """A stand-in for cluster-bans on one host."""

    def __init__(self, banned=(), down=False):
        self.banned, self.down, self.calls = list(banned), down, []

    def __call__(self, *words):
        self.calls.append(words)
        if self.down:
            raise requests.Failed("no route to host")
        if words[0] == "list":
            return self.banned
        hit = [b for b in self.banned if b["ip"] == words[1]]
        self.banned = [b for b in self.banned if b["ip"] != words[1]]
        return {"ip": words[1], "unbanned": len(hit)}


class Unban(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.hosts = {"login": Host([BAN]), "node1": Host()}
        self.engine = requests.Engine(
            {"unban": bans.UnbanFeature(self.hosts)}, os.path.join(tmp.name, "p.json")
        )

    def req(self, ip="192.0.2.7"):
        return {"kind": "unban", "user": "alice", "ip": ip, "reason": "wrong password"}

    def test_a_banned_address_waits_for_an_administrator_then_goes_everywhere(self):
        held = self.engine.submit(self.req())
        self.assertEqual(held.status, "held")
        self.assertIn("login", held.text)
        self.assertIn("2026-11-20 03:12:44", held.text)
        done = self.engine.approve(held.key, "boss")
        self.assertEqual(done.status, "done")
        self.assertEqual(self.hosts["login"].banned, [])
        self.assertIn(("unban", "192.0.2.7"), self.hosts["node1"].calls, "every host is asked")

    def test_an_address_not_banned_or_not_an_address_is_refused(self):
        self.assertEqual(self.engine.submit(self.req("198.51.100.1")).status, "refused")
        self.assertEqual(self.engine.submit(self.req("nonsense")).status, "refused")

    def test_a_host_that_cannot_be_reached_is_said(self):
        self.hosts["node1"].down = True
        held = self.engine.submit(self.req())
        self.assertEqual(held.status, "held", "the hosts that answered are enough to ask")
        done = self.engine.approve(held.key, "boss")
        self.assertIn("node1", done.text)
        self.hosts["login"].down = True
        nobody = self.engine.submit(self.req())
        self.assertEqual(nobody.status, "refused", "no host answered: nothing to ask an administrator")
        self.assertIn("no route to host", nobody.text)


class Services(unittest.TestCase):
    def test_the_bot_asks_for_the_services_the_roles_offer(self):
        for role, var, name in (("scratch_alloc", "_scratch_alloc_bot_service", scratch.SERVICE),
                                ("fail2ban", "_fail2ban_bot_service", bans.SERVICE)):  # fmt: skip
            offered = yaml.safe_load((ROOT / "roles" / role / "vars/main.yml").read_text())[var]["name"]
            self.assertEqual(offered, name, role)

    def test_the_request_goes_as_the_service_and_its_quoted_words(self):
        done = mock.Mock(returncode=0, stdout='{"ok": 1}', stderr="")
        with mock.patch("subprocess.run", return_value=done) as run:
            out = remote.Remote("10.0.0.5", "/k", "bot", "/kh", "scratch")(
                "create", "alice", "2T", 30, "two words"
            )
        self.assertEqual(out, {"ok": 1})
        argv = run.call_args.args[0]
        self.assertEqual(argv[-2:], ["bot@10.0.0.5", "scratch create alice 2T 30 'two words'"])
        self.assertIn("StrictHostKeyChecking=yes", argv)
        refused = mock.Mock(returncode=2, stdout="", stderr="scratch-alloc: no allocation 5\n")
        with mock.patch("subprocess.run", return_value=refused), self.assertRaises(requests.Failed) as e:
            remote.Remote("10.0.0.5", "/k", "bot", "/kh", "scratch")("release", 5)
        self.assertEqual(str(e.exception), "no allocation 5")


if __name__ == "__main__":
    unittest.main()
