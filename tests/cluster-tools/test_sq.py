"""Tests for sq (clustertools.sq) on a busy-cluster scenario built over
the real Slurm 25.11 fixture (see scenario.py). The cases follow the shell version's
regression tests, then the behaviour the Python version adds."""

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import scenario
from common import HERE, LIB, run_tool

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
        text = out("--ascii", "--no-resources", "--no-users")
        self.assertRegex(text, r"\(QOSMaxGRESPerJob\)")
        self.assertRegex(text, r"(?m)^> +90010 ")

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
    def test_a_failing_controller_is_one_line_and_exit_1(self):
        r = sq(env={"FAKE_FAIL": "slurm_load_node error: Unable to contact slurm controller"})
        self.assertEqual(r.returncode, 1)
        self.assertEqual(
            r.stderr.strip(), "sq: scontrol: slurm_load_node error: Unable to contact slurm controller"
        )

    def test_a_stalled_controller_is_bounded(self):
        r = sq(env={"FAKE_SLEEP": "3", "CLUSTER_TOOLS_TIMEOUT": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("no answer in 1 s (slurmctld slow, or rate limiting", r.stderr)

    def test_the_interval_backs_off_while_slurm_fails(self):
        code = f"import sys; sys.path.insert(0, {LIB!r}); from clustertools import sq; sys.exit(sq.main())"
        env = {"PATH": f"{HERE / 'bin'}:/usr/bin:/bin", "FIXTURES": str(SCENE), "LANG": "C.UTF-8",
               "USER": "user1", "FAKE_FAIL": "down"}  # fmt: skip
        try:
            r = subprocess.run(["timeout", "3", "python3", "-I", "-c", code, "-i", "2"], env=env,
                               capture_output=True, text=True)  # fmt: skip
        except FileNotFoundError:
            self.skipTest("no timeout(1)")
        self.assertIn("Slurm not answering, next try in 4s", r.stdout)

    def test_no_scontrol_is_127(self):
        self.assertEqual(sq(path=False).returncode, 127)


if __name__ == "__main__":
    unittest.main()
