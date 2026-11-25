"""cluster_bot.bans through the request engine: an unban is asked only for an address
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
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
from cluster_bot import bans, remote, requests  # noqa: E402
from cluster_tools import rich  # noqa: E402

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


class Channel:
    """The admin channel: messages posted, edited, pinned, and one that can be deleted."""

    def __init__(self):
        self.posted, self.updated, self.pinned, self.deleted = [], [], [], set()

    def post(self, text, blocks, pin=False):
        message = {"channel": "CADMIN", "ts": str(len(self.posted))}
        self.posted.append(text)
        if pin:
            self.pinned.append(message["ts"])
        return message

    def update(self, message, text, blocks):
        if message["ts"] in self.deleted:
            return False
        self.updated.append(text)
        return True


class Watch(unittest.TestCase):
    def setUp(self):
        self.hosts = {"login": Host([BAN]), "node1": Host()}
        self.feature, self.channel, self.seen = bans.UnbanFeature(self.hosts), Channel(), {}

    def round(self):
        bans.watch(self.feature, self.channel.post, self.channel.update, self.seen)

    def test_the_first_look_learns_what_is_banned_and_lists_it_once(self):
        self.round()
        self.assertEqual(self.channel.posted, ["현재 차단된 주소 1개"], "no notice for bans made before")
        self.assertEqual(self.channel.pinned, ["0"])
        self.round()
        self.assertEqual(len(self.channel.posted), 1, "the list is edited in place")
        self.assertEqual(self.channel.updated, ["현재 차단된 주소 1개"])

    def test_new_bans_are_told_in_one_message_and_the_list_follows(self):
        self.round()
        wave = [
            {"ip": f"198.51.100.{i}", "jail": "sshd", "since": f"2026-11-22 10:00:{i:02d}"} for i in range(60)
        ]
        self.hosts["node1"].banned = wave
        self.round()
        self.assertEqual(self.channel.posted[-1], "새로 차단된 주소 60개", "a wave is one message, not sixty")
        self.assertEqual(self.channel.updated[-1], "현재 차단된 주소 61개")
        text, blocks = bans.bans_blocks.ban_list([{**b, "host": "node1"} for b in wave], {})
        self.assertIn("외 20개", str(blocks), "a long list is cut and counted")
        self.assertTrue(all(len(rich.read([b])) < 3000 for b in blocks))

    def test_a_host_that_cannot_be_read_keeps_its_bans_and_is_named(self):
        self.round()
        self.hosts["login"].down = True
        self.round()
        self.assertIn("login sshd 192.0.2.7", self.seen["bans"])
        self.assertEqual(self.channel.updated[-1], "현재 차단된 주소 1개", "listed as last seen")
        self.hosts["login"].down = False
        self.round()
        self.assertEqual(self.channel.posted, ["현재 차단된 주소 1개"], "back again is not new")

    def test_a_deleted_list_is_posted_again(self):
        self.round()
        self.channel.deleted.add("0")
        self.round()
        self.assertEqual(self.channel.posted, ["현재 차단된 주소 1개"] * 2)
        self.assertEqual(self.seen["list"]["ts"], "1")


class Services(unittest.TestCase):
    def test_the_bot_asks_for_the_services_the_roles_offer(self):
        from cluster_bot import data

        used = {s["name"]: s for s in yaml.safe_load((ROOT / "roles/cluster_bot/vars/main.yml").read_text())[
            "_cluster_bot_services"]}  # fmt: skip
        for role, var, name in (("fail2ban", "_fail2ban_bot_service", bans.SERVICE),
                                ("shared_space", "_shared_space_bot_service", data.SERVICE)):  # fmt: skip
            offered = yaml.safe_load((ROOT / "roles" / role / "vars/main.yml").read_text())[var]["name"]
            self.assertEqual(offered, name, role)
            # The deploy's check names the playbook that offers it: one that runs the role.
            playbook = ROOT / used[name]["playbook"]
            self.assertIn(f"role: {role}", playbook.read_text(), playbook)
        self.assertEqual(sorted(used), sorted([bans.SERVICE, data.SERVICE]))

    def test_the_request_goes_as_the_service_and_its_quoted_words(self):
        done = mock.Mock(returncode=0, stdout='{"ok": 1}', stderr="")
        with mock.patch("subprocess.run", return_value=done) as run:
            out = remote.Remote("10.0.0.5", "/k", "bot", "/kh", "bans")("unban", "203.0.113.7", "two words")
        self.assertEqual(out, {"ok": 1})
        argv = run.call_args.args[0]
        self.assertEqual(argv[-2:], ["bot@10.0.0.5", "bans unban 203.0.113.7 'two words'"])
        self.assertIn("StrictHostKeyChecking=yes", argv)
        refused = mock.Mock(returncode=2, stdout="", stderr="cluster-bans: not banned 203.0.113.8\n")
        with mock.patch("subprocess.run", return_value=refused), self.assertRaises(requests.Failed) as e:
            remote.Remote("10.0.0.5", "/k", "bot", "/kh", "bans")("unban", "203.0.113.8")
        self.assertEqual(str(e.exception), "not banned 203.0.113.8")
        # A reply that is not JSON is that host's failure, never an exception past it.
        garbled = mock.Mock(returncode=0, stdout="Welcome!\n", stderr="")
        with mock.patch("subprocess.run", return_value=garbled), self.assertRaises(requests.Failed) as e:
            remote.Remote("10.0.0.5", "/k", "bot", "/kh", "bans")("unban", "203.0.113.9")
        self.assertIn("Welcome!", str(e.exception))

    def test_the_client_is_the_bots_own_never_the_sites_ssh_config(self):
        """The site's ssh_config turns on host-based authentication; inherited, the bot's
        ssh tried it first and its failure ("sign using hostkey ... failed") hid why."""
        argv = remote.Remote("10.0.0.5", "/k", "bot", "/kh", "bans").argv("bans list")
        self.assertEqual(argv[1:3], ["-F", "none"])
        for option in ("IdentitiesOnly=yes", "PreferredAuthentications=publickey", "BatchMode=yes",
                       "StrictHostKeyChecking=yes", "UserKnownHostsFile=/kh", "GlobalKnownHostsFile=none"):  # fmt: skip
            self.assertIn(option, argv)
        self.assertEqual(argv[argv.index("-i") + 1], "/k")


# What ssh said for each of the 9 hosts on 2026-11-25 (the owner's #admin paste).
DENIED = (
    "no reply\nsign using hostkey ssh-ed25519 SHA256:AAAA failed\n"
    "cluster-bot@192.0.2.10: Permission denied (publickey,password,hostbased).\n"
)


class Reasons(unittest.TestCase):
    def test_each_failure_is_one_short_line_with_what_to_do(self):
        cases = (
            (255, DENIED, "봇의 키가 허용되지 않았습니다", "cluster-bot.yml"),
            (
                255,
                "Host key verification failed.\n",
                "호스트 키가 봇이 아는 것과 다릅니다",
                "cluster-bot.yml",
            ),
            (
                255,
                "ssh: connect to host 192.0.2.10 port 22: Connection timed out\n",
                "접속되지 않습니다",
                "sshd",
            ),
            (
                2,
                "cluster-bot-serve: this host offers no 'bans'\n",
                "이 서비스를 제공하지 않습니다",
                "cluster-bot.yml",
            ),
            (2, "cluster-bans: not banned 203.0.113.8\n", "not banned 203.0.113.8", ""),
        )
        for code, stderr, reason, fix in cases:
            with self.subTest(reason):
                e = remote.failure(code, stderr)
                self.assertEqual(str(e), reason)
                self.assertNotIn("\n", str(e))
                self.assertIn(fix, e.fix)

    def test_the_hosts_of_one_reason_are_one_line(self):
        names = [f"gpu{i}" for i in range(1, 9)] + ["master"]
        errors = {h: remote.failure(255, DENIED) for h in names}
        errors["login"] = remote.failure(255, "Connection refused")
        self.assertEqual(
            bans.bans_blocks.by_reason(errors),
            "gpu[1-8],master: 봇의 키가 허용되지 않았습니다; login: 접속되지 않습니다",
        )

    def test_the_list_of_no_host_read_says_so_once(self):
        """2026-11-25: "현재 차단된 주소 0개", then "없음", then nine raw SSH errors."""
        errors = {h: remote.failure(255, DENIED) for h in [f"gpu{i}" for i in range(1, 9)] + ["master"]}
        text, blocks = bans.bans_blocks.ban_list([], errors, read=False)
        shown = str(blocks)
        self.assertEqual(text, "차단 목록을 읽지 못했습니다")
        self.assertNotIn("없음", shown)
        self.assertNotIn("0개", shown)
        self.assertNotIn("Permission denied", shown)
        self.assertEqual(shown.count("gpu[1-8],master"), 1)
        self.assertIn("cluster-bot.yml", shown)
        text, blocks = bans.bans_blocks.ban_list([], {})
        self.assertEqual(text, "현재 차단된 주소가 없습니다")
        self.assertEqual([b["type"] for b in blocks], ["rich_text", "context"], "said once, in the header")


if __name__ == "__main__":
    unittest.main()
