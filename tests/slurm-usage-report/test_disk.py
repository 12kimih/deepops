"""The daily disk usage report, cluster_tools.disk_report, and its run in
cluster_tools.usage_report --disk (no cluster needed)."""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROLES = Path(__file__).resolve().parents[2] / "roles"
sys.path.insert(0, str(ROLES / "cluster_tools_lib/files"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests/lib"))
from cluster_tools import disk_report, rich  # noqa: E402
from cluster_tools import usage_report as report  # noqa: E402
from fake_slack import FakeDM  # noqa: E402

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


def text_of(blocks):
    return rich.read([b for b in blocks if b["type"] != "header"])


class CollectTests(unittest.TestCase):
    def test_usage_levels_growth_and_disks(self):
        data = disk_report.collect(site(), NOW)
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
        disk_report.collect(prom, NOW)
        disk = [e for e in prom.asked if "node_filesystem_size_bytes" in e][0]
        self.assertIn("and on (instance, mountpoint) label_replace(", disk)
        self.assertIn('"mountpoint", "$1", "filesystem"', disk)

    def test_nothing_counted_is_none(self):
        self.assertIsNone(disk_report.collect(Prom({}), NOW))

    def test_a_host_label_or_the_instance(self):
        self.assertEqual(disk_report.host_of({"host": "a", "instance": "b:9100"}), "a")
        self.assertEqual(disk_report.host_of({"instance": "b:9100"}), "b")


class LevelTests(unittest.TestCase):
    def entry(self, **kw):
        return dict({"used": 0, "warn": 0, "hard": 0, "inodes": 0, "inode_warn": 0, "inode_hard": 0}, **kw)

    def test_hard_first_then_warn_and_files_too(self):
        self.assertEqual(disk_report.past(self.entry(used=20, warn=15, hard=20))[0][3], "hard")
        self.assertEqual(disk_report.past(self.entry(used=16, warn=15, hard=20)), [("space", 16, 15, "warn")])
        self.assertEqual(
            disk_report.past(self.entry(used=15, warn=15, hard=20)), [], "at the warn level is not past it"
        )
        self.assertEqual(
            disk_report.past(self.entry(inodes=600, inode_warn=500)), [("files", 600, 500, "warn")]
        )
        self.assertEqual(disk_report.past(self.entry(used=99)), [], "no levels, nothing past")

    def test_worst_first(self):
        data = disk_report.collect(site(), NOW)
        self.assertEqual([r[2] for r in disk_report.over_warn(data)], ["bob", "alice"])


class RenderTests(unittest.TestCase):
    def test_card_and_thread(self):
        data = disk_report.collect(site(), NOW)
        (text, card), thread = disk_report.render(data)
        self.assertIn("login:/ 75%", text)
        self.assertIn("경고 수준 초과 2건", text)
        body = text_of(card)
        # Owner 2026-11-28: a part per filesystem, its gauge, then its people as bullets,
        # named as text (the status channel mentions no one: decision 12).
        login = body[body.index("login:/\n") : body.index("nfs:/home\n")]
        self.assertIn("`████████░░  75%` 100G 남음 (크기 400G), 하루 사이 +10G", login)
        self.assertIn(
            f"{disk_report.PAST_TITLE}\n- :small_red_triangle: bob: 21G, 한도(20G)에 닿았습니다", login
        )
        self.assertIn("- :small_orange_diamond: alice: 18G, 경고 수준(15G)을 넘었습니다 (한도 20G)", login)
        self.assertNotIn("carol", body)
        self.assertNotIn("<@", str(card), "a record never mentions")
        self.assertIn("nfs (마지막", body, "a host not reported lately is named")
        self.assertNotIn("login (last", body)
        details = text_of(thread[0][1])
        self.assertIn("login:/ 사용자별 사용량 (계정 3개)", details)
        self.assertIn("carol", details, "every person, none cut")
        self.assertNotIn("외 ", details)
        self.assertIn("alice   18G     +3G", details)
        self.assertIn("/home/shared/datasets  nfs:/home  7G      0", details)
        for blocks in [card] + [b for _, b in thread]:
            self.assertLessEqual(len(blocks), 50)

    def test_a_filling_filesystem_names_its_largest_people_as_bullets(self):
        data = disk_report.collect(site(), NOW)
        self.assertEqual(disk_report.fill_rows(data, 0.8), [], "75% full is below 80%")
        fill = disk_report.fill_rows(data, 0.7, n=2)
        self.assertEqual(
            [(k, u) for k, _, u in fill], [(("login", "/"), [("bob", 21 * G), ("alice", 18 * G)])]
        )
        (_, card), _ = disk_report.render(data, fill=fill)
        self.assertIn("여유 공간 부족 (75% 사용, 가장 많이 쓰는 분)\n- bob: 21G\n- alice: 18G", text_of(card),
                      "a bullet each, named as text, never a comma run")  # fmt: skip
        self.assertIn("DM으로", text_of(card), "the card says how they are told")

    def test_a_project_past_its_warn_level_is_on_the_card(self):
        # The shared data's budget (cluster_tools.shared_data): 20G hard, warned at 90%.
        prom = site()
        prom.now["disk_quota_project_used_bytes"].append(
            row(19 * G, host="nfs", filesystem="/home", project="shared-data")
        )
        prom.now["disk_quota_project_limit_bytes"] += [
            row(20 * G, host="nfs", filesystem="/home", project="shared-data", kind="hard"),
            row(18 * G, host="nfs", filesystem="/home", project="shared-data", kind="warn"),
        ]
        (_, card), _ = disk_report.render(disk_report.collect(prom, NOW))
        body = text_of(card)
        self.assertIn(disk_report.PROJECTS_TITLE, body)
        self.assertIn(
            ":small_orange_diamond: shared-data nfs:/home에서: 19G, 경고 수준(18G)을 넘었습니다 (한도 20G)",
            body,
        )
        self.assertNotIn("/home/shared/datasets*", body, "7G of a 10G limit is past nothing")

    def test_folders_worth_cleaning_in_the_thread(self):
        prom = site()
        prom.now["disk_cleanup_candidate_bytes"] = [
            row(300 * G, host="nfs", filesystem="/home", path="/home/alice/old", user="alice")
        ]
        prom.now["disk_cleanup_candidate_unused_days"] = [
            row(120, host="nfs", filesystem="/home", path="/home/alice/old", user="alice")
        ]
        data = disk_report.collect(prom, NOW)
        _, thread = disk_report.render(data, stale_days=60)
        details = text_of(thread[0][1])
        self.assertIn(f"nfs:/home {disk_report.CLEANUP_TITLE}", details)
        self.assertIn("60일 넘게 쓰지 않은 폴더, 크고 오래된 순", details)
        self.assertIn("/home/alice/old  alice  300G  120일", details)

    def test_people_ranked_everyone_else_in_a_table_of_their_own(self):
        data = disk_report.collect(site(), NOW)
        data["users"][("login", "/", "root")] = dict(data["users"][("login", "/", "carol")], used=160 * G)
        data["users"][("login", "/", "mysql")] = dict(data["users"][("login", "/", "carol")], used=16 * G)
        _, thread = disk_report.render(data, people={"alice", "bob", "carol"})
        details = text_of(thread[0][1])
        self.assertIn("login:/ 사용자별 사용량 (3명)", details)
        people, others = details.split(f"login:/ {disk_report.OTHERS_TITLE}")
        self.assertNotIn("root", people, "root is not ranked with the people")
        self.assertIn(" (2개, 합계 176G)", others)
        self.assertIn("명부에 없는 계정: root, 서비스 계정, 떠난 분의 계정입니다.", others)
        self.assertIn("root   160G", others)
        self.assertIn("mysql  16G", others)
        _, thread = disk_report.render(data)
        self.assertIn("사용자별 사용량 (계정 5개)", text_of(thread[0][1]), "no roster: everyone is ranked")

    def test_a_hard_limit_says_what_it_means_for_slurm(self):
        data = disk_report.collect(site(), NOW)
        (_, card), _ = disk_report.render(data, holds=True)
        self.assertIn(". 공간을 비우실 때까지 새 Slurm 잡을 제출할 수 없습니다", text_of(card))
        (_, card), _ = disk_report.render(data)
        self.assertNotIn("Slurm", text_of(card), "no holds: nothing said")
        bob = disk_report.over_warn(data)[0]
        self.assertIn("지금은 그곳에 쓸 수 없습니다. 공간을 비우실 때까지 새 Slurm 잡을 제출할 수 없습니다.",
                      disk_report.dm_text(bob, disk_report.hold_note(True, bob[3])))  # fmt: skip

    def test_an_announced_limit_is_told_now_with_its_day(self):
        """Owner 2026-11-28: people are told against the announced limits at once; writes
        are refused and jobs held only from the limit's day (disk_quota "from")."""
        prom = site()
        day = NOW + 2 * disk_report.DAY
        prom.now["disk_quota_limit_from_timestamp_seconds"] = [
            row(day, host="login", filesystem="/", user=u) for u in ("alice", "bob", "carol")
        ]
        data = disk_report.collect(prom, NOW)
        start = f"{datetime.fromtimestamp(day):%Y-%m-%d}부터"
        bob, alice = disk_report.over_warn(data)
        self.assertEqual(bob[4][0][3], "hard", "past the announced hard: told daily, as at a hard limit")
        self.assertFalse(disk_report.enforced(bob[3]))
        (_, card), _ = disk_report.render(data, holds=True)
        text = text_of(card)
        self.assertIn(
            f"{start} 적용되는 한도(20G)를 넘었습니다. {start}는 한도에 닿아 있는 동안 새 Slurm 잡을", text
        )
        self.assertIn(f"경고 수준(15G)을 넘었습니다 (한도 20G). 한도는 {start} 적용됩니다", text)
        self.assertIn(f"아직 적용되지 않은 한도는 {start} 적용되니", text)
        self.assertNotIn("닿았습니다", text, "nothing is refused yet")
        dm = disk_report.dm_text(bob, disk_report.hold_note(True, bob[3]))
        self.assertIn("아직 막힌 것은 없습니다. 한도가 적용되기 전에 공간을 비워 주세요.", dm)
        self.assertNotIn("쓸 수 없습니다.", dm)

    def test_change(self):
        self.assertEqual(disk_report.change(5 * G, None), "신규")
        self.assertEqual(disk_report.change(5 * G, 5 * G + 1000), "0")
        self.assertEqual(disk_report.change(4 * G, 5 * G), "-1G")

    def test_dm(self):
        data = disk_report.collect(site(), NOW)
        rows = disk_report.over_warn(data)
        self.assertEqual(
            disk_report.dm_text(rows[1]),
            "login:/의 디스크 사용량이 18G입니다. 경고 수준(15G)을 넘었습니다 (한도 20G). 아직 막힌 것은"
            " 없습니다. 한도에 닿기 전에 공간을 비워 주세요.\n내 사용량은 `usage disk`로 보실 수 있습니다.",
        )
        self.assertIn(
            "지금은 그곳에 쓸 수 없습니다. 공간을 비우시면 다시 쓰실 수 있습니다.",
            disk_report.dm_text(rows[0]),
        )


class RunTests(unittest.TestCase):
    """usage_report --disk: posts the report naming everyone past a level as text, tells
    each by DM when due (decision 12: never a mention), and posts nothing when no disk
    usage is counted."""

    CFG = dict(channel="#status", dm_log_channel="", prometheus_url="", slack_token="t", email_domain="",
               user_map={"alice": "UALICE", "bob": "UBOB"},
               disk_repeat_days=7, people=["alice", "bob", "carol"], holds_on=False, disk_fill=0.8, disk_fill_top=5,
               stale_days=60,
               admin_channel="#admins", admin_mention="<!subteam^SADMINS>")  # fmt: skip

    def run_disk(self, prom, *extra, state=None, now=NOW):
        """(posts [(channel, text, blocks as text)], the failure, disk.json): one run at
        NOW, keeping its state in the directory STATE (a fresh one when None)."""
        posted = []

        class Slack(FakeDM):
            token = "t"

            def __init__(self, *_, **__):
                pass

            def post(self, channel, text, blocks=None, thread_ts=None, cls=None, key=None, repeat=None):
                if channel == "UBOB":
                    raise RuntimeError("Slack chat.postMessage failed: channel_not_found")
                posted.append((channel, text, rich.read(blocks)))
                return "ts"

        with tempfile.TemporaryDirectory() as fresh:
            d = state or fresh
            cfg = dict(self.CFG, state_file=f"{d}/state.json")
            args = mock.Mock(dry_run=False, to=None)
            for flag in extra:
                setattr(args, *flag)
            with (
                mock.patch.object(report, "Prometheus", lambda url: prom),
                mock.patch.object(report, "Slack", Slack),
                mock.patch.object(report.time, "time", lambda: now),
            ):
                try:
                    report.disk(cfg, args)
                    error = None
                except RuntimeError as e:
                    error = str(e)
            saved = Path(f"{d}/disk.json")
            return posted, error, json.loads(saved.read_text()) if saved.exists() else None

    def test_report_and_notes_one_failure_named_at_the_end(self):
        posted, error, saved = self.run_disk(site())
        self.assertEqual(posted[0][0], "#status")
        self.assertEqual([p[0] for p in posted if p[0].startswith("U")], ["UALICE"])
        self.assertIn("notifying bob", error)
        self.assertEqual(saved["users"]["alice"]["id"], "UALICE")

    def test_told_by_dm_once_then_again_when_due_and_named_on_the_card_every_day(self):
        with tempfile.TemporaryDirectory() as d:
            posted, _, saved = self.run_disk(site(), state=d)
            self.assertEqual(set(saved["told"]), {"login / alice"}, "bob's DM failed: not told")
            self.assertEqual([p[0] for p in posted if p[0].startswith("U")], ["UALICE"])
            self.assertNotIn("<@", posted[0][2])
            # The next day: bob is still at his hard limit (tried again), alice past the
            # same warn level (not again within disk_repeat_days); both named on the card.
            posted, error, _ = self.run_disk(site(), state=d, now=NOW + disk_report.DAY)
            self.assertIn("notifying bob", error)
            self.assertEqual([p[0] for p in posted if p[0].startswith("U")], [])
            self.assertIn(":small_orange_diamond: alice:", posted[0][2])
            self.assertIn(":small_red_triangle: bob:", posted[0][2])

    def test_a_filling_filesystems_largest_people_get_a_dm_at_most_weekly(self):
        with tempfile.TemporaryDirectory() as d:
            self.CFG = dict(RunTests.CFG, disk_fill=0.7, user_map={"alice": "UALICE", "carol": "UCAROL"})
            posted, _, saved = self.run_disk(site(), state=d)
            self.assertEqual(saved["told"]["fill login /"], NOW)
            fill_dms = [p for p in posted if "공간이 75% 찼습니다" in p[1]]
            self.assertEqual(
                {p[0] for p in fill_dms}, {"UALICE", "UCAROL"}, "every largest person with a member"
            )
            self.assertIn("- bob: 21G", posted[0][2], "named on the card")
            posted, _, _ = self.run_disk(site(), state=d, now=NOW + disk_report.DAY)
            self.assertFalse([p for p in posted if "찼습니다" in p[1]], "not again the next day")
            self.assertIn("- bob: 21G", posted[0][2], "named every day")
            posted, _, _ = self.run_disk(site(), state=d, now=NOW + 7 * disk_report.DAY)
            self.assertTrue([p for p in posted if "찼습니다" in p[1]], "again a week on")
            self.CFG = RunTests.CFG

    def test_a_preview_sends_and_records_nothing(self):
        posted, error, saved = self.run_disk(site(), ("to", "UME"))
        self.assertEqual({p[0] for p in posted}, {"UME"})
        self.assertIsNone(error)
        self.assertIsNone(saved)

    def test_scratch_cleanup_tells_each_person_once_per_date_and_admins_of_a_burst(self):
        prom = site()
        prom.now.update({
            "scratch_space_purge_due_files": [row(3, host="gpu8", user="alice")],
            "scratch_space_purge_due_bytes": [row(2 * G, host="gpu8", user="alice")],
            "scratch_space_purge_next_timestamp_seconds": [row(NOW + 14 * disk_report.DAY, host="gpu8", user="alice")],
            "scratch_space_purge_stale_days": [row(60, host="gpu8")],
            "scratch_space_purge_burst_files": [row(5000, host="gpu8", user="carol")],
        })  # fmt: skip
        with tempfile.TemporaryDirectory() as d:
            posted, _, saved = self.run_disk(prom, state=d)
            dm = [p for p in posted if p[0] == "UALICE" and "스크래치 공간" in p[1]]
            self.assertEqual(len(dm), 1)
            self.assertIn("스크래치 공간의 파일 3개(2G)", dm[0][1])
            self.assertIn("`usage disk`", dm[0][1])
            admin = [p for p in posted if p[0] == self.CFG["admin_channel"] and "carol" in p[1]]
            self.assertEqual(len(admin), 1)
            self.assertTrue(admin[0][1].startswith("<!subteam^SADMINS> "), "the administrators are mentioned")
            self.assertEqual(len(saved["purge_told"]), 2)
            # The next day, the same date: told already.
            posted, _, _ = self.run_disk(prom, state=d, now=NOW + disk_report.DAY)
            self.assertFalse([p for p in posted if "스크래치 공간" in p[1] and p[0] == "UALICE"])

    def test_a_deferral_for_running_jobs_is_said_in_the_notice(self):
        prom = site()
        prom.now.update({
            "scratch_space_purge_due_files": [row(3, host="gpu8", user="alice")],
            "scratch_space_purge_due_bytes": [row(2 * G, host="gpu8", user="alice")],
            "scratch_space_purge_next_timestamp_seconds": [row(NOW + 60 * disk_report.DAY, host="gpu8", user="alice")],
            "scratch_space_purge_stale_days": [row(60, host="gpu8")],
            "scratch_space_purge_deferred_files": [row(2, host="gpu8", user="alice")],
        })  # fmt: skip
        posted, _, _ = self.run_disk(prom)
        dm = [p for p in posted if p[0] == "UALICE" and "스크래치 공간" in p[1]]
        self.assertIn("그중 2개는 실행 중인 잡이 있어 미뤄졌습니다", dm[0][1])
        self.assertIn("잡이 계속 실행 중이어도 미뤄진 날짜에는 정리되니", dm[0][1])

    def test_nothing_counted_posts_nothing(self):
        posted, error, _ = self.run_disk(Prom({}))
        self.assertEqual(posted, [])
        self.assertIsNone(error)


if __name__ == "__main__":
    unittest.main()
