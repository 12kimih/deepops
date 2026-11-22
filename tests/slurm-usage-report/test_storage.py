"""The daily disk usage report, clustertools.storage_report, and its run in
clustertools.usage_report --storage (no cluster needed)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROLES = Path(__file__).resolve().parents[2] / "roles"
sys.path.insert(0, str(ROLES / "cluster_tools_lib/files"))
from clustertools import storage_report as storage  # noqa: E402
from clustertools import usage_report as report  # noqa: E402

NOW = 1_800_000_000
G = 2**30


def row(value, **labels):
    return {
        "metric": {"instance": f"{labels.get('host', 'login')}:9100", **labels},
        "value": [NOW, str(value)],
    }


class Prom:
    """Answers by metric name, a day ago when asked at NOW - DAY."""

    def __init__(self, now, before=None, disks=True):
        self.now, self.before, self.disks, self.asked = now, before or {}, disks, []

    def query(self, expr, at=None):
        self.asked.append(expr)
        answers = self.before if at is not None else self.now
        if expr.startswith("max by (instance, host, mountpoint)"):
            if not self.disks:
                return []
            name = expr.split("(", 2)[2].split(")")[0]
            return answers.get(name, [])
        name = expr.split("{")[0]
        kind = expr.split('kind="')[1].split('"')[0] if 'kind="' in expr else None
        return [r for r in answers.get(name, []) if kind is None or r["metric"].get("kind") == kind]


def site():
    now = {
        "disk_quota_used_bytes": [
            row(18 * G, host="login", filesystem="/", user="alice"),
            row(21 * G, host="login", filesystem="/", user="bob"),
            row(2 * G, host="login", filesystem="/", user="carol"),
        ],
        "disk_quota_limit_bytes": [
            row(15 * G, host="login", filesystem="/", user=u, kind="warn") for u in ("alice", "bob", "carol")
        ]
        + [row(20 * G, host="login", filesystem="/", user=u, kind="hard") for u in ("alice", "bob", "carol")],
        "disk_quota_used_inodes": [
            row(10, host="login", filesystem="/", user=u) for u in ("alice", "bob", "carol")
        ],
        "disk_quota_project_used_bytes": [
            row(7 * G, host="nfs", filesystem="/home", project="/home/shared/datasets", project_id="101")
        ],
        "disk_quota_project_limit_bytes": [
            row(10 * G, host="nfs", filesystem="/home", project="/home/shared/datasets", kind="hard")
        ],
        "node_filesystem_size_bytes": [
            row(400 * G, host="login", mountpoint="/"),
            row(1000 * G, host="nfs", mountpoint="/home"),
        ],  # fmt: skip
        "node_filesystem_avail_bytes": [
            row(100 * G, host="login", mountpoint="/"),
            row(500 * G, host="nfs", mountpoint="/home"),
        ],  # fmt: skip
        "disk_quota_last_success_timestamp_seconds": [
            row(NOW - 60, host="login"),
            row(NOW - 7200, host="nfs"),
        ],
    }
    before = {
        "disk_quota_used_bytes": [
            row(15 * G, host="login", filesystem="/", user="alice"),
            row(21 * G, host="login", filesystem="/", user="bob"),
        ],  # fmt: skip
        "disk_quota_project_used_bytes": [
            row(7 * G, host="nfs", filesystem="/home", project="/home/shared/datasets", project_id="101")
        ],
        "node_filesystem_avail_bytes": [row(110 * G, host="login", mountpoint="/")],
    }
    return Prom(now, before)


def who(user, mention):
    return f"@{user}" if mention else user


def text_of(blocks):
    return "\n".join(
        b["text"]["text"] if b["type"] != "context" else b["elements"][0]["text"]
        for b in blocks
        if b["type"] != "header"
    )


class CollectTests(unittest.TestCase):
    def test_usage_levels_growth_and_disks(self):
        data = storage.collect(site(), NOW)
        alice = data["users"][("login", "/", "alice")]
        self.assertEqual(
            (alice["used"], alice["before"], alice["warn"], alice["hard"]), (18 * G, 15 * G, 15 * G, 20 * G)
        )
        self.assertIsNone(data["users"][("login", "/", "carol")]["before"], "not counted a day ago")
        self.assertEqual(
            data["disks"][("login", "/")], {"size": 400 * G, "avail": 100 * G, "avail_before": 110 * G}
        )
        self.assertEqual(set(data["disks"]), {("login", "/"), ("nfs", "/home")})
        self.assertEqual(data["projects"][("nfs", "/home", "/home/shared/datasets")]["hard"], 10 * G)

    def test_the_disks_are_only_those_counted(self):
        prom = site()
        storage.collect(prom, NOW)
        disk = [e for e in prom.asked if "node_filesystem_size_bytes" in e][0]
        self.assertIn("and on (instance, mountpoint) label_replace(", disk)
        self.assertIn('"mountpoint", "$1", "filesystem"', disk)

    def test_nothing_counted_is_none(self):
        self.assertIsNone(storage.collect(Prom({}), NOW))

    def test_a_host_label_or_the_instance(self):
        self.assertEqual(storage.host_of({"host": "a", "instance": "b:9100"}), "a")
        self.assertEqual(storage.host_of({"instance": "b:9100"}), "b")


class LevelTests(unittest.TestCase):
    def entry(self, **kw):
        return dict({"used": 0, "warn": 0, "hard": 0, "inodes": 0, "inode_warn": 0, "inode_hard": 0}, **kw)

    def test_hard_first_then_warn_and_files_too(self):
        self.assertEqual(storage.past(self.entry(used=20, warn=15, hard=20))[0][3], "hard")
        self.assertEqual(storage.past(self.entry(used=16, warn=15, hard=20)), [("space", 16, 15, "warn")])
        self.assertEqual(
            storage.past(self.entry(used=15, warn=15, hard=20)), [], "at the warn level is not past it"
        )
        self.assertEqual(storage.past(self.entry(inodes=600, inode_warn=500)), [("files", 600, 500, "warn")])
        self.assertEqual(storage.past(self.entry(used=99)), [], "no levels, nothing past")

    def test_worst_first(self):
        data = storage.collect(site(), NOW)
        self.assertEqual([r[2] for r in storage.over_warn(data)], ["bob", "alice"])


class RenderTests(unittest.TestCase):
    def test_card_and_thread(self):
        data = storage.collect(site(), NOW)
        (text, card), thread = storage.render(data, who, top=2)
        self.assertIn("login:/ 75%", text)
        self.assertIn("2 past their level", text)
        body = text_of(card)
        self.assertIn("100G free of 400G, +10G used in a day", body)
        self.assertIn(":no_entry: @bob on login:/: 21G, at the hard limit 20G: writes there fail now", body)
        self.assertIn(":warning: @alice on login:/: 18G, past the warn level 15G, hard limit 20G", body)
        self.assertNotIn("@carol", body)
        self.assertIn("nfs (last", body, "a host not reported lately is named")
        self.assertNotIn("login (last", body)
        details = text_of(thread[0][1])
        self.assertIn("the largest of 3 users", details)
        self.assertIn("1 more, 2G together", details)
        self.assertIn("alice  18G   +3G", details)
        self.assertIn("/home/shared/datasets  nfs:/home  7G    0", details)
        for blocks in [card] + [b for _, b in thread]:
            self.assertLessEqual(len(blocks), 50)

    def test_people_ranked_everyone_else_on_one_line(self):
        data = storage.collect(site(), NOW)
        data["users"][("login", "/", "root")] = dict(data["users"][("login", "/", "carol")], used=160 * G)
        data["users"][("login", "/", "mysql")] = dict(data["users"][("login", "/", "carol")], used=16 * G)
        _, thread = storage.render(data, who, top=10, people={"alice", "bob", "carol"})
        details = text_of(thread[0][1])
        self.assertIn("the largest of 3 people", details)
        self.assertNotIn("root  ", details, "root is not ranked with the people")
        self.assertIn(
            "Not in the roster (root, services, people who left): root 160G, mysql 16G; 176G together",
            details,
        )
        _, thread = storage.render(data, who, top=10)
        self.assertIn("the largest of 5 users", text_of(thread[0][1]), "no roster: everyone is ranked")

    def test_change(self):
        self.assertEqual(storage.change(5 * G, None), "new")
        self.assertEqual(storage.change(5 * G, 5 * G + 1000), "0")
        self.assertEqual(storage.change(4 * G, 5 * G), "-1G")

    def test_dm(self):
        data = storage.collect(site(), NOW)
        rows = storage.over_warn(data)
        self.assertEqual(
            storage.dm_text(rows[1]),
            "Your disk use on login:/ is 18G, past the warn level 15G, hard limit 20G. Nothing is refused yet:"
            " free space before the hard limit. `quota -s` on the host shows your own usage there.",
        )
        self.assertIn("Writes there fail until you free space.", storage.dm_text(rows[0]))


class DueTests(unittest.TestCase):
    """Who is told: a first crossing at once, a hard limit every day, the same warn level
    every repeat_days; someone back under a level is forgotten."""

    def test_policy(self):
        rows = storage.over_warn(storage.collect(site(), NOW))
        told = {}
        self.assertEqual([r[2] for r in storage.due(rows, told, NOW, 7)], ["bob", "alice"])
        for r in rows:
            told[storage.row_key(r)] = {"kind": r[4][0][3], "at": NOW}
        self.assertEqual([r[2] for r in storage.due(rows, told, NOW + storage.DAY, 7)], ["bob"])
        # A daily timer can run a little early: the seventh day still counts.
        self.assertEqual(len(storage.due(rows, told, NOW + 7 * storage.DAY - 600, 7)), 2)
        self.assertEqual(len(storage.due(rows, told, NOW + storage.DAY, 0)), 2, "0: every day")
        storage.due([r for r in rows if r[2] == "bob"], told, NOW, 7)
        self.assertEqual(set(told), {"login / bob"}, "alice is back under: forgotten")


class RunTests(unittest.TestCase):
    """usage_report --storage: posts the report, notes everyone past a level when asked,
    and posts nothing when no disk usage is counted."""

    CFG = dict(channel="#status", prometheus_url="", slack_token="t", email_domain="",
               user_map={"alice": "UALICE", "bob": "UBOB"}, storage_top=10, storage_notify=["mention", "dm"],
               storage_repeat_days=7, people=["alice", "bob", "carol"])  # fmt: skip

    def run_storage(self, prom, *extra, notify=("mention", "dm"), state=None, now=NOW):
        """(posts [(channel, text, blocks as text)], the failure, storage.json): one run at
        NOW, keeping its state in the directory STATE (a fresh one when None)."""
        posted = []

        class Slack:
            token = "t"

            def __init__(self, *_):
                pass

            def channel(self, target):
                return target

            def post(self, channel, text, blocks=None, thread_ts=None):
                if channel == "UBOB":
                    raise RuntimeError("Slack chat.postMessage failed: channel_not_found")
                posted.append((channel, text, json.dumps(blocks)))
                return "ts"

        with tempfile.TemporaryDirectory() as fresh:
            d = state or fresh
            cfg = dict(self.CFG, state_file=f"{d}/state.json", storage_notify=list(notify))
            args = mock.Mock(dry_run=False, to=None)
            for flag in extra:
                setattr(args, *flag)
            with (
                mock.patch.object(report, "Prometheus", lambda url: prom),
                mock.patch.object(report, "Slack", Slack),
                mock.patch.object(report.time, "time", lambda: now),
            ):
                try:
                    report.storage(cfg, args)
                    error = None
                except RuntimeError as e:
                    error = str(e)
            saved = Path(f"{d}/storage.json")
            return posted, error, json.loads(saved.read_text()) if saved.exists() else None

    def test_report_and_notes_one_failure_named_at_the_end(self):
        posted, error, saved = self.run_storage(site())
        self.assertEqual(posted[0][0], "#status")
        self.assertEqual([p[0] for p in posted if p[0].startswith("U")], ["UALICE"])
        self.assertIn("notifying bob", error)
        self.assertEqual(saved["users"]["alice"]["id"], "UALICE")

    def test_told_once_then_again_when_due_and_the_card_mentions_only_them(self):
        with tempfile.TemporaryDirectory() as d:
            posted, _, saved = self.run_storage(site(), notify=["mention"], state=d)
            self.assertEqual(set(saved["told"]), {"login / alice", "login / bob"})
            self.assertEqual(saved["told"]["login / bob"], {"kind": "hard", "at": NOW})
            self.assertIn("<@UALICE> (alice)", posted[0][2])
            # The next day: bob is still at his hard limit, alice past the same warn level.
            posted, _, _ = self.run_storage(site(), notify=["mention"], state=d, now=NOW + storage.DAY)
            self.assertIn("<@UBOB> (bob)", posted[0][2])
            self.assertNotIn("<@UALICE>", posted[0][2])
            self.assertIn("alice on login:/", posted[0][2], "still listed, not mentioned")

    def test_a_preview_sends_and_records_nothing(self):
        posted, error, saved = self.run_storage(site(), ("to", "UME"))
        self.assertEqual({p[0] for p in posted}, {"UME"})
        self.assertIsNone(error)
        self.assertIsNone(saved)

    def test_no_notes_without_dm(self):
        posted, error, _ = self.run_storage(site(), notify=["mention"])
        self.assertEqual({p[0] for p in posted}, {"#status"})
        self.assertIsNone(error)

    def test_nothing_counted_posts_nothing(self):
        posted, error, _ = self.run_storage(Prom({}))
        self.assertEqual(posted, [])
        self.assertIsNone(error)


if __name__ == "__main__":
    unittest.main()
