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

sys.path.insert(0, str(ROOT / "tests/lib"))
from fake_slack import FakeChannel  # noqa: E402

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


class Watch(unittest.TestCase):
    def setUp(self):
        self.hosts = {"login": Host([BAN]), "node1": Host()}
        self.feature, self.slack, self.seen, self.said = bans.UnbanFeature(self.hosts), FakeChannel(), {}, []

    def round(self):
        bans.watch(self.feature, self.slack, "#admin", self.seen, self.said.append)

    def lists(self):
        return [m for m in self.slack.posts() if m["text"].startswith(("Banned addresses", "현재"))]

    def day_logs(self):
        return [m for m in self.slack.posts() if m["text"].startswith("Ban log ")]

    def logged(self):
        """What the day logs' threads show."""
        return "\n".join(rich.read(r["blocks"]) for d in self.day_logs() for r in self.slack.thread(d["ts"]))

    def test_the_first_look_learns_what_is_banned_and_lists_it_once(self):
        self.round()
        self.assertEqual(
            [m["text"] for m in self.slack.posts()],
            ["Banned addresses: 1"],
            "no log for bans made before",
        )
        self.assertEqual(self.slack.pins, {self.lists()[0]["ts"]})
        self.round()
        self.assertEqual(len(self.slack.posts()), 1, "the list is edited in place")

    def test_new_bans_go_in_the_days_log_every_one_mentioning_no_one(self):
        self.round()
        wave = [{"ip": f"198.51.100.{i}", "jail": "sshd", "since": f"2026-11-22 10:{i // 60:02d}:{i % 60:02d}"}
                for i in range(200)]  # fmt: skip
        self.hosts["node1"].banned = wave
        self.round()
        (day,) = self.day_logs()
        shown = self.logged()
        self.assertIn("New bans: 200", shown)
        self.assertTrue(all(b["ip"] in shown for b in wave), "every new ban, none cut")
        self.assertNotIn("<!", shown + day["text"], "a log mentions no one")
        (listed,) = self.lists()
        self.assertEqual(listed["text"], "Banned addresses: 201")
        self.hosts["node1"].banned = wave + [{**BAN, "ip": "198.51.100.250"}]
        self.round()
        self.assertEqual(len(self.day_logs()), 1, "one message a day")
        self.assertIn(": 2 rounds", self.day_logs()[0]["text"])

    def test_the_list_is_a_summary_and_its_thread_every_ban(self):
        """2026-11-26: "외 17개 (가장 최근 40개만 보입니다)", and a body far too long."""
        many = [{"ip": f"203.0.113.{i}", "jail": "sshd", "since": f"2026-11-{1 + i // 10:02d} 00:00:{i % 60:02d}"}
                for i in range(250)]  # fmt: skip
        self.hosts["login"].banned = many
        self.round()
        (listed,) = self.lists()
        body = rich.read(listed["blocks"])
        self.assertNotIn("외 ", body)
        self.assertNotIn("가장 최근 40", body)
        self.assertIn("By host\n- login: 250", body)
        self.assertIn("Newest: `203.0.113.249`", body)
        self.assertLess(len(body), 600, "the body is a summary")
        replies = self.slack.thread(listed["ts"])
        shown = "\n".join(rich.read(r["blocks"]) for r in replies)
        self.assertTrue(all(f"`{b['ip']}`" in shown for b in many), "every ban in the thread")
        self.assertLess(shown.index("203.0.113.249"), shown.index("203.0.113.0"), "newest first")
        self.hosts["login"].banned = many[:3]
        self.round()
        replies = self.slack.thread(listed["ts"])
        self.assertEqual(len(replies), 1, "the extra replies are deleted")
        self.assertEqual(rich.read(replies[0]["blocks"]).count("`203.0.113."), 3)

    def test_a_lost_record_finds_the_list_and_posts_no_second(self):
        """2026-11-26: two copies of the list in the admin channel."""
        self.round()
        self.seen.pop("list")
        self.slack.__dict__.pop("_kept_scanned")  # a restart
        self.round()
        self.assertEqual(len(self.lists()), 1)

    def test_two_lists_from_before_the_mark_become_one(self):
        old = self.slack.add("현재 차단된 주소 57개")
        newer = self.slack.add("현재 차단된 주소 57개")
        self.slack.add("현재 차단된 주소 3개", bot=("B9", "A9"))  # another app's
        self.seen.update(bans={}, list={"channel": "CADMIN", "ts": old})
        self.round()
        mine = [m for m in self.lists() if m["bot_id"] == "B1"]
        self.assertEqual([m["ts"] for m in mine], [old], "the recorded copy kept, the other deleted")
        self.assertNotIn(newer, [m["ts"] for m in self.slack.posts()])
        self.assertEqual(len(self.lists()), 2, "another app's message is not the bot's to delete")

    def test_a_host_that_cannot_be_read_keeps_its_bans_and_is_named(self):
        self.round()
        self.hosts["login"].down = True
        self.round()
        self.assertIn("login sshd 192.0.2.7", self.seen["bans"])
        (listed,) = self.lists()
        self.assertEqual(listed["text"], "Banned addresses: 1", "listed as last seen")
        self.assertIn("login", rich.read(listed["blocks"]))
        self.hosts["login"].down = False
        self.round()
        self.assertEqual(
            [m["text"] for m in self.slack.posts()], ["Banned addresses: 1"], "back again is not new"
        )

    def test_a_first_round_that_read_no_host_tells_nothing_new_after(self):
        """The bot's key was refused on every host in its first round (2026-11-26): the
        next round, the first that read them, told all 57 bans as new."""
        for h in self.hosts.values():
            h.down = True
        self.round()
        for h in self.hosts.values():
            h.down = False
        self.round()
        self.assertEqual([m["text"] for m in self.slack.posts()], ["Banned addresses: 1"], "no log")
        self.hosts["node1"].banned = [{**BAN, "ip": "198.51.100.9"}]
        self.round()
        self.assertIn("New bans: 1", self.logged())

    def test_a_host_read_for_the_first_time_tells_nothing_new(self):
        self.hosts["node1"].down = True
        self.round()
        self.hosts["node1"].down, self.hosts["node1"].banned = False, [{**BAN, "ip": "198.51.100.9"}]
        self.round()
        self.assertEqual(self.day_logs(), [], "what it banned before is not new")

    def test_a_record_from_before_read_keeps_telling_new_bans(self):
        self.round()
        del self.seen["read"]
        self.hosts["login"].banned = [BAN, {**BAN, "ip": "198.51.100.9"}]
        self.round()
        self.assertIn("New bans: 1", self.logged())

    def test_a_deleted_list_is_posted_again(self):
        self.round()
        self.slack.messages.clear()
        self.round()
        self.assertEqual(len(self.lists()), 1)


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
            (255, DENIED, "the bot's key is not accepted", "cluster-bot.yml"),
            (
                255,
                "Host key verification failed.\n",
                "the host key differs from the one the bot knows",
                "cluster-bot.yml",
            ),
            (
                255,
                "ssh: connect to host 192.0.2.10 port 22: Connection timed out\n",
                "cannot connect",
                "sshd",
            ),
            (
                2,
                "cluster-bot-serve: this host offers no 'bans'\n",
                "the host does not offer this service",
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
            "gpu[1-8],master: the bot's key is not accepted; login: cannot connect",
        )

    def test_the_list_of_no_host_read_says_so_once(self):
        """2026-11-25: "현재 차단된 주소 0개", then "없음", then nine raw SSH errors."""
        errors = {h: remote.failure(255, DENIED) for h in [f"gpu{i}" for i in range(1, 9)] + ["master"]}
        (text, blocks), _ = bans.bans_blocks.ban_list([], errors, read=False)
        shown = str(blocks)
        self.assertEqual(text, "Banned addresses: unread")
        self.assertNotIn("none", shown)
        self.assertNotIn(": 0", rich.read(blocks))
        self.assertNotIn("Permission denied", shown)
        self.assertEqual(shown.count("gpu[1-8],master"), 1)
        self.assertIn("cluster-bot.yml", shown)
        (text, blocks), _ = bans.bans_blocks.ban_list([], {})
        self.assertEqual(text, "Banned addresses: none")
        self.assertEqual([b["type"] for b in blocks], ["header", "context"], "said once, in the header")


if __name__ == "__main__":
    unittest.main()
