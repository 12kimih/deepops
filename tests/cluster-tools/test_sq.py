"""Tests for sq (cluster_tools.sq) on a busy-cluster scenario built over
the real Slurm 25.11 fixture (see scenario.py). The cases follow the shell version's
regression tests, then the behaviour the Python version adds."""

import io
import json
import re
import shutil
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import scenario
from cluster_tools import sq as sq_tool, vocab
from common import run_tool

TMP = tempfile.TemporaryDirectory()
SCENE = Path(TMP.name)
scenario.write(SCENE)
ANSI = re.compile(r"\033\[[0-9;]*m")
TYPOGRAPHIC = re.compile("[–—·…]| -- ")


def sq(*args, env=None, **kw):
    return run_tool("sq", *args, env={"FIXTURES": str(SCENE), "USER": "user1", **(env or {})}, **kw)


def out(*args, env=None):
    r = sq(*args, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def model(*args, env=None):
    return json.loads(out("--json", *args, env=env))


def row(data, kind, name):
    return next(r for r in data["resources"] if r["kind"] == kind and r["name"] == name)


class FigureTests(unittest.TestCase):
    """What the shell version's TSV checks asserted, from the JSON model."""

    @classmethod
    def setUpClass(cls):
        cls.m = model()

    def test_cluster_figures_come_from_the_nodes(self):
        c = self.m["resources"][0]
        self.assertEqual(
            (c["kind"], c["cpu_used"], c["gres_used"]), ("cluster", 40 + 92 + 20 + 48, 4 + 8 + 2 + 3)
        )
        self.assertEqual(c["used_from"], "nodes")

    def test_free_skips_drain_and_down(self):
        c = self.m["resources"][0]
        # gpu1 4, gpu3 8, gpu5 8, gpu6 5, gpu8 4 open; gpu2 full; gpu4 drain, gpu7 down.
        self.assertEqual(c["free"], 4 + 8 + 8 + 5 + 4)
        self.assertEqual(c["off"], 2)

    def test_node_classes(self):
        self.assertEqual(row(self.m, "node", "gpu5")["class"], "idle")
        self.assertEqual(row(self.m, "node", "gpu4")["class"], "drain")
        self.assertEqual(row(self.m, "node", "gpu4")["free"], 0)
        self.assertEqual(row(self.m, "node", "gpu7")["class"], "down")

    def test_a_shared_pool_counts_its_own_jobs(self):
        a30 = row(self.m, "family", "a30")
        self.assertEqual(a30["used_from"], "jobs")
        self.assertEqual((a30["cpu_used"], a30["gres_used"]), (92 + 32 + 20, 8 + 2 + 2))
        self.assertEqual(row(self.m, "part", "debug")["cpu_used"], 16)

    def test_an_exclusive_pool_counts_its_nodes(self):
        self.assertEqual(row(self.m, "family", "a10")["used_from"], "nodes")


class TableTests(unittest.TestCase):
    def test_a_finished_job_is_not_charged_or_listed(self):
        users = {u["user"]: u for u in model()["users"]}
        self.assertNotIn("user9", users)
        self.assertEqual(users["user1"]["running"], 2)
        self.assertEqual(users["user1"]["cpu"], 72)

    def test_reason_code_and_own_jobs_marked(self):
        text = out("-a", "--ascii", "--no-resources", "--no-users")
        self.assertRegex(text, r"\(QOSMaxGRESPerJob\)")
        self.assertRegex(text, r"(?m)^> +90010 ")

    def test_why_is_an_unbroken_tree(self):
        """Every line of the WHY table is a row of it: family, code, leaf, rule or total.
        A sentence between rows breaks the tree lines and the columns."""
        for args in ([], ["-d"]):
            with self.subTest(args=args):
                text = out("-W", "--no-queue", "--no-resources", "--no-users", "--ascii", "--wide", *args)
                body = text.split("\n")[3:-2]  # after heading, header and rule; before the blank
                titles = "|".join(vocab.REASON_TITLE.values())
                for line in body:
                    self.assertRegex(line, rf"^. ((\|  |   )?(\|- |`- )|{titles}|total|-+$)", line)

    def test_filters_split_on_commas_only(self):
        # squeue's rule: a job name may hold spaces.
        name = "x UserId=root(0) JobState=RUNNING Partition=evil"
        self.assertEqual([j["id"] for j in model("-n", name)["jobs"]], [90002])

    def test_an_array_task_is_found_inside_its_pending_record(self):
        self.assertEqual([j["label"] for j in model("-j", "84151_2")["jobs"]], ["84151_[0-3]"])
        self.assertEqual(model("-j", "84151_7")["jobs"], [])

    def test_one_partition_set_is_one_group(self):
        # The fixture has the same pending sets written in both orders.
        why = model()["pending"]
        spellings = {r["partition"] for r in why}
        self.assertIn("a30-1,a30-2", spellings)
        self.assertNotIn("a30-2,a30-1", spellings)
        keys = [(r["code"], r["partition"], r["user"]) for r in why]
        self.assertEqual(len(keys), len(set(keys)))

    def test_detail_goes_in_a_column(self):
        why = out("-W", "-d", "--no-queue", "--no-resources", "--no-users", "--ascii", "--wide")
        self.assertIn("DETAIL", why.split("\n")[1])
        self.assertRegex(why, r"ReqNodeNotAvail .+ReqNodeNotAvail, May be reserved")
        self.assertNotIn("higher-priority jobs are ahead", why)  # family help is --help's

    def test_why_groups_codes_into_families(self):
        text = out("-W", "--no-queue", "--no-resources", "--no-users")
        self.assertRegex(text, r"(?m)^ +request over a limit")
        self.assertIn("QOSMaxGRESPerJob", text)
        self.assertNotIn("USER", text.split("\n")[1], "no USER column without --detail")

    def test_detail_adds_who_and_full_states(self):
        why = out("-W", "-d", "--no-queue", "--no-resources", "--no-users")
        self.assertRegex(why, r"a30-1,a30-2 +user2")
        nodes = out("-d", "-N", "--no-queue", "--no-users")
        self.assertRegex(nodes, r"gpu7 +down\+not_responding")

    def test_a_node_reads_as_sinfo_prints_it(self):
        """2026-11-26: sinfo said idle* (not responding) where sq said down. A node's
        state is sinfo's word; its colour and the free count still follow its class."""
        nodes = ANSI.sub("", out("-N", "--no-queue", "--no-users"))
        for name, short in (("gpu7", "down*"), ("gpu5", "idle~"), ("gpu4", "drng"), ("gpu2", "alloc")):
            with self.subTest(node=name):
                self.assertRegex(nodes, rf"(?m)^\W*{name} +{re.escape(short)} ")
        self.assertEqual(row(model(), "node", "gpu7")["class"], "down")

    def test_users_by_default_and_no_legends(self):
        text = out()
        self.assertIn("USERS", text)
        self.assertNotRegex(text, "closed to new jobs|count nodes|shares nodes")

    def test_pending_array_counts_tasks(self):
        why = model()["pending"]
        tasks = sum(r["jobs"] for r in why)
        records = sum(1 for j in model()["jobs"] if j["state"] == "PD")
        self.assertGreater(tasks, records)

    def test_running_time_is_shown_as_squeue_does(self):
        self.assertRegex(out("--no-users", "--no-resources", "-u", "user4"), r"(?m) 1:0\d:\d\d +1-00:00:00")

    def test_a_hostile_job_name_is_only_a_name(self):
        job = next(j for j in model()["jobs"] if j["id"] == 90002)
        self.assertEqual((job["user"], job["state"], job["partition"][:3]), ("user3", "PD", "a30"))


class GpuUserTests(unittest.TestCase):
    """USERS on a typed-GPU cluster: the plain table, then each person's team and, per GPU
    type, what they hold under a high QoS and a low one as high/low."""

    def users(self):
        return {u["user"]: u for u in model()["users"]}

    def table(self, *args, env=None):
        return ANSI.sub("", out("--no-queue", "--no-resources", "--no-why", "--ascii", *args, env=env))

    def test_gpus_split_by_type_and_qos_class(self):
        self.assertEqual(self.users()["user1"]["gpus"], {"high": {"a10": 4, "a30": 2}, "low": {}})

    def test_the_team_is_the_persons_not_the_jobs_account(self):
        u = self.users()
        self.assertEqual(u["user1"]["teams"], ["acct5"])  # their jobs run under acct1
        self.assertEqual(u["user2"]["teams"], ["acct4"])  # theirs under acct1 and acct16

    def test_a_team_is_an_account_granting_a_high_qos_its_parent_does_not(self):
        classes = {"low": {"high": False}, "high_a": {"high": True}}
        assoc = {"associations": [
            {"account": "root", "parent_account": "", "qos": ["low"]},
            {"account": "admin", "parent_account": "root", "qos": ["low"]},
            {"account": "a", "parent_account": "root", "qos": ["high_a", "low"]},
            {"account": "a1", "parent_account": "a", "qos": ["high_a", "low"]},
            {"user": "lead", "account": "admin", "qos": ["high_a", "low"]},
            {"user": "lead", "account": "a1", "qos": ["high_a", "low"]},
            {"user": "staff", "account": "admin", "qos": ["high_a", "low"]},
        ]}  # fmt: skip
        self.assertEqual(sq_tool._user_teams(assoc, classes), {"lead": ["a1"]})  # staff: no team, "-"

    def test_who_then_jobs_then_tres_order_then_the_share(self):
        text = self.table()
        self.assertRegex(text, r"USER +TEAM +RUN +PEND +NODES +CPU +MEMORY +GPU +HIGH +LOW +HIGH +LOW +SHARE")
        # Each GPU type names its HIGH and LOW columns on the line above, over both.
        lines = text.splitlines()
        head = next(i for i, line in enumerate(lines) if line.split()[:1] == ["USER"])
        group, cols = lines[head - 1], lines[head]
        for label, first in (("A10", cols.index(" HIGH ")), ("A30", cols.rindex(" HIGH "))):
            self.assertLessEqual(group.index(label) - 2, first + 6, label)
            self.assertGreaterEqual(group.index(label), first, label)
        self.assertRegex(text, r"(?m)^. user1 +acct5 +\d+ +\d+ +\d+ +\d+ +\d+G +6 +4 +- +2 +- ")
        self.assertRegex(text, r"(?m)^  total +\d+ +\d+ +\d+ +\d+ +\d+G +17 +4 +0 +13 +0$")
        self.assertNotRegex(text, r"\(H/L\)|hi/lo|HOLDERS")

    def test_teams_show_each_high_qos_quota_as_the_qos_command_counts_it(self):
        teams = {r["qos"]: r for r in model()["teams"]}
        self.assertEqual(teams["high_t1"], {"team": "t1", "qos": "high_t1",
                                            "gpu": {"a10": {"used": 4, "limit": 0}, "a30": {"used": 13, "limit": 6}}})  # fmt: skip
        qos = run_tool("qos", "--json", "--all", env={"FIXTURES": str(SCENE), "USER": "user1"})
        qos_rows = {r["name"]: r for r in json.loads(qos.stdout)["qos"]}
        for name, r in teams.items():
            self.assertEqual(r["gpu"], qos_rows[name]["gpu"], name)

    def test_teams_rows_line_up_and_add_up(self):
        text = self.table()
        block = text[text.index("TEAMS") :]
        rows = [line for line in block.splitlines() if re.match(r"  (t\d|total) ", line)]
        self.assertEqual([r.split()[0] for r in rows], ["t1", "t2", "t3", "t4", "t5", "total"])
        # Every quota cell has one shape: its bar starts in the same column on every row.
        starts = {re.search(r"\d+/\d+ [#-]", r).end() - 1 for r in rows}
        self.assertEqual(len(starts), 1, rows)
        self.assertRegex(rows[-1], r"total +4/28 [#-]{10} +13/31 [#-]{10}$")
        self.assertRegex(rows[0], r"t1 +high_t1 +4/0 -{10} +13/6 #{10}")

    def test_without_the_qos_the_split_is_said_to_be_unknown(self):
        broken = SCENE / "noqos"
        if not broken.exists():
            shutil.copytree(SCENE, broken)
            (broken / "qos.json").write_text("{not json")
        r = sq("--no-queue", "--no-resources", "--ascii", env={"FIXTURES": str(broken)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("QoS unread", r.stdout)
        self.assertNotIn("TEAM ", r.stdout)


class ColumnOrderTests(unittest.TestCase):
    """Every table reads alike: who, state, then resources in Slurm's TRES order (nodes,
    CPUs, memory, GPUs and their breakdown), time or the summary last."""

    def header(self, first, *args):
        text = ANSI.sub("", out("--ascii", *args, env={"COLUMNS": "200"}))
        return next(line.split() for line in text.splitlines() if line.split()[:1] == [first])

    def test_jobs(self):
        self.assertEqual(self.header("JOBID", "-a", "--no-users", "--no-resources", "--no-why"),
                         ["JOBID", "NAME", "USER", "ST", "PARTITION", "QOS", "N", "CPU", "GRES", "NODE(REASON)",
                          "TIME", "LIMIT", "PRIO"])  # fmt: skip

    def test_resources(self):
        self.assertEqual(self.header("NAME", "--no-queue", "--no-users", "--no-why"),
                         ["NAME", "STATE", "IDLE", "OFF", "CPU", "MEMORY", "GPU", "FREE"])  # fmt: skip

    def test_why(self):
        self.assertEqual(self.header("REASON", "--no-queue", "--no-users", "--no-resources", "-W", "-d")[:5],
                         ["REASON", "USER", "JOBS", "NODES", "CPU"])  # fmt: skip


class QueueTests(unittest.TestCase):
    """The job list shows your own jobs by default; the sections below summarise all."""

    def ids(self, text):
        return set(re.findall(r"(?m)^[ >▌] +(\d+) ", ANSI.sub("", text)))

    def test_default_is_your_own_jobs(self):
        self.assertEqual(self.ids(out("--ascii", "--no-users", "--no-resources")), {"90010", "90012"})

    def test_all_jobs_on_request(self):
        everyone = self.ids(out("-a", "--ascii", "--no-users", "--no-resources"))
        self.assertTrue({"90010", "90011", "90013", "90001"} <= everyone)
        self.assertEqual(everyone, self.ids(out("--queue", "all", "--ascii", "--no-users", "--no-resources")))
        self.assertEqual(everyone, self.ids(out("--all", "--ascii", "--no-users", "--no-resources")))

    def test_the_renamed_options_still_work_and_say_their_new_names(self):
        r = sq("-Q", "-T", "--ascii", "--no-users")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("sq: -Q is now -a / --all", r.stderr)
        self.assertIn("sq: -T is now -P / --partitions", r.stderr)
        self.assertEqual(self.ids(r.stdout), self.ids(out("-a", "-P", "--ascii", "--no-users")))

    def test_a_job_filter_shows_what_it_selects(self):
        self.assertEqual(self.ids(out("-u", "user4", "--ascii", "--no-users", "--no-resources")), {"90013"})

    def test_the_rest_still_covers_everyone(self):
        self.assertIn("user2", out("--no-resources"))  # USERS lists everyone by default

    def test_none_of_yours_and_none_at_all(self):
        text = out("--no-users", "--no-resources", env={"USER": "nobody"})
        self.assertIn("none of yours", text)
        self.assertNotIn("JOBID", text)
        self.assertNotIn("JOBS", out("--queue", "none", "--no-users", "--no-resources"))
        self.assertNotIn("JOBS", out("--no-queue", "--no-users", "--no-resources"))

    def test_json_is_not_narrowed_by_the_view(self):
        self.assertIn(90011, {j["id"] for j in model()["jobs"]})

    def test_queue_values_complete(self):
        self.assertIn('--queue) COMPREPLY=($(compgen -W "$(printf', out("--completion", "bash"))


class FilterTests(unittest.TestCase):
    def jobs(self, *args):
        return {j["id"] for j in model(*args)["jobs"]}

    def test_squeue_filters(self):
        self.assertEqual(self.jobs("-u", "user4"), {90013})
        self.assertEqual(self.jobs("-j", "90011,90013"), {90011, 90013})
        self.assertEqual(self.jobs("--me", "-t", "R"), {90010, 90012})
        self.assertIn(90003, self.jobs("-t", "all"))
        self.assertNotIn(90003, self.jobs())
        self.assertEqual(self.jobs("-n", "toobig"), {90001})
        self.assertTrue(self.jobs("-p", "debug") == {90013})

    def test_filters_apply_to_every_section(self):
        m = model("-u", "user4")
        self.assertEqual([u["user"] for u in m["users"]], ["user4"])
        self.assertEqual(m["pending"], [])

    def test_partition_filter_limits_node_rows(self):
        text = out("-p", "a10*", "-N", "--no-queue", "--no-users")
        self.assertNotIn("gpu2", text)
        self.assertIn("gpu1", text)

    def test_nodelist_takes_a_hostlist(self):
        text = out("-w", "gpu[1-2,8]", "-N", "--no-queue", "--no-users")
        self.assertIn("gpu8", text)
        self.assertNotIn("gpu3 ", text)

    def test_nodelist_hides_pools_with_no_node_left(self):
        text = out("-w", "gpu8", "--no-queue", "--no-users")
        self.assertIn("a10", text)
        self.assertNotRegex(text, r"(?m)^  a30 ")


class OptionTests(unittest.TestCase):
    def test_sq_options_and_the_last_word(self):
        text = out(env={"SQ_OPTIONS": "--no-users --no-queue"})
        self.assertNotIn("USERS", text)
        self.assertIn("RESOURCES", text)
        self.assertIn("USERS", out("--users", env={"SQ_OPTIONS": "--no-users"}))

    def test_json_ignores_sq_options(self):
        ids = [j["id"] for j in model(env={"SQ_OPTIONS": "-u user4"})["jobs"]]
        self.assertEqual(ids, [j["id"] for j in model()["jobs"]])
        self.assertGreater(len(ids), 1)

    def test_squeue_format_options_are_refused_with_a_reason(self):
        for args in (["-o", "%i"], ["-o%i"], ["--format=%i"], ["-l"], ["--start"]):
            with self.subTest(args=args):
                r = sq(*args)
                self.assertEqual(r.returncode, 2)
                self.assertIn("use squeue itself", r.stderr)
        self.assertIn("--no-queue", sq("--no-jobs").stderr)

    def test_help_levels_and_keys(self):
        self.assertIn("More: sq --help", out("-h"))
        full = out("--help")
        self.assertRegex(full, r"(?m)^  drain ")
        self.assertRegex(full, r"(?m)^  limit reached")

    def test_interval_floor(self):
        self.assertEqual(sq("-i", "1").returncode, 2)

    def test_no_typographic_punctuation(self):
        for args in ([], ["-W", "-d", "-N"], ["--split"], ["--help"]):
            with self.subTest(args=args):
                self.assertIsNone(TYPOGRAPHIC.search(ANSI.sub("", out("--color", "always", *args))))
        # In ASCII the rules themselves are drawn with "-", so only the characters count there.
        self.assertIsNone(re.search("[\u2013\u2014\u00b7\u2026]", out("--ascii", "-W")))


class FailureTests(unittest.TestCase):
    def test_a_failing_controller_is_one_line_and_exit_3(self):
        r = sq(env={"FAKE_FAIL": "slurm_load_node error: Unable to contact slurm controller"})
        self.assertEqual(r.returncode, 3)
        self.assertEqual(
            r.stderr.strip(), "sq: scontrol: slurm_load_node error: Unable to contact slurm controller"
        )

    def test_a_stalled_controller_is_bounded(self):
        r = sq(env={"FAKE_SLEEP": "3", "CLUSTER_TOOLS_TIMEOUT": "1"})
        self.assertEqual(r.returncode, 3)
        self.assertIn("no answer in 1 s (slurmctld slow, or rate limiting", r.stderr)

    def test_the_interval_backs_off_while_slurm_fails(self):
        # In-process, the clock and the reads faked: a real process under timeout(1) printed
        # nothing in time on a loaded host (2026-11-24). Doubling from the interval, capped.
        waits = []

        def sleep(seconds):
            waits.append(seconds)
            if len(waits) == 7:
                raise KeyboardInterrupt

        args, theme = sq_tool.COMMAND.parse(["-i", "2", "--no-color"])
        out = io.StringIO()
        with mock.patch.object(sq_tool, "frame", side_effect=sq_tool.slurm.Error("down")), \
                mock.patch.object(sq_tool.time, "sleep", sleep), mock.patch.object(sq_tool.sys, "stdout", out):  # fmt: skip
            self.assertEqual(sq_tool.watch(theme, args), 0)
        self.assertEqual(waits, [4, 8, 16, 32, 60, 60, 60])
        self.assertIn("Slurm read failed, next try in 4s", out.getvalue())

    def test_no_nodes_is_said_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            for f in SCENE.iterdir():
                (Path(d) / f.name).write_text(f.read_text())
            data = json.loads((Path(d) / "nodes.json").read_text())
            (Path(d) / "nodes.json").write_text(json.dumps({**data, "nodes": []}))
            for args in ((), ("--json",)):
                r = sq(*args, env={"FIXTURES": d})
                self.assertEqual(r.returncode, 3, r.stderr)
                self.assertNotIn("Traceback", r.stderr)
            self.assertIn("No node data", sq(env={"FIXTURES": d}).stdout)

    def test_no_scontrol_could_not_run(self):
        self.assertEqual(sq(path=False).returncode, 2)


if __name__ == "__main__":
    unittest.main()
