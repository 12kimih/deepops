"""scripts/docs/site_facts.py: the docs' facts from code and config, and the public file
holding only what PUBLIC allows (the user docs site is published)."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("site_facts", ROOT / "scripts/docs/site_facts.py")
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)


def site(tmp, all_yml="", host_vars=None):
    config = Path(tmp) / "config"
    (config / "group_vars").mkdir(parents=True)
    (config / "host_vars").mkdir()
    (config / "group_vars" / "all.yml").write_text(all_yml)
    for host, text in (host_vars or {}).items():
        (config / "host_vars" / host).write_text(text)
    return config


class Facts(unittest.TestCase):
    def test_every_cluster_command_has_its_korean_summary(self):
        """The user docs and the Slack channel guides list the commands in Korean."""
        with tempfile.TemporaryDirectory() as tmp:
            names = sf.settings(site(tmp)).get("cluster_tools_builtin")
        self.assertTrue(names)
        for name, c in sf.commands(names).items():
            self.assertTrue(c["summary_ko"], f"{name}: Command(summary_ko=...)")

    def test_the_bots_subcommands_in_order_with_their_korean_help(self):
        out = sf.bot({"cluster_bot_command": "/c"})
        self.assertEqual(out["command"], "/c")
        self.assertEqual(out["subcommands"][-1]["name"], "help")
        self.assertTrue(all(s["help"] for s in out["subcommands"]))

    def test_commands_come_from_their_one_definition(self):
        got = sf.commands(["sq"])["sq"]
        flags = [f for o in got["options"] for f in o["flags"]]
        self.assertTrue(got["summary"] and got["usage"].startswith("sq"))
        self.assertIn("-u", flags)

    def test_the_site_overrides_a_default_and_templates_and_vaulted_values_stay_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = site(tmp, "cluster_user_stale_days: 30\nslack_admin_group: \"{{ x }}\"\nsecret: !vault |\n  abc\n",
                          {"node1": "disk_quota_filesystems: {/data: {block_hard: 1T}}\n"})  # fmt: skip
            every = sf.facts(config)
        self.assertEqual(every["policy"]["stale_days"], 30)
        self.assertEqual(every["policy"]["grace_days"], 14, "the role default where the site sets none")
        self.assertEqual(every["policy"]["cancel_after_hours"], 0, "the role's default: no cancel")
        self.assertEqual(
            every["policy"]["idle_notify"], ["mention"], "how idle owners are told, the role default"
        )
        self.assertEqual(every["policy"]["leave_grace_days"], 30, "a template over the site's stale_days")
        self.assertIsNone(every["chat"]["admin_group"], "a template is Ansible's to resolve")
        self.assertEqual(
            every["quotas"]["hosts"], {"node1": {"filesystems": {"/data": {"block_hard": "1T"}}}}
        )


class DocsUrl(unittest.TestCase):
    """The docs site's site_url and the config's cluster_info.docs_url were never
    compared (2026-11-27): the banner, the bot and the agent rules send people to one."""

    def check(self, site_url, docs_url):
        with tempfile.TemporaryDirectory() as tmp:
            config = site(tmp, f"cluster_info: {{docs_url: '{docs_url}'}}\n")
            docs = Path(tmp) / "docs-site"
            (docs / "data").mkdir(parents=True)
            (docs / "zensical.toml").write_text(f'[project]\nsite_url = "{site_url}"\n')
            return sf.docs_url_mismatch(docs / "data" / "site.json", config)

    def test_the_same_address_passes_a_trailing_slash_aside(self):
        self.assertIsNone(self.check("https://docs.example/guide/", "https://docs.example/guide"))

    def test_another_address_is_named(self):
        self.assertIn(
            "is not cluster_info.docs_url", self.check("https://docs.example/a/", "https://docs.example/b/")
        )

    def test_an_unset_docs_url_is_not_compared(self):
        self.assertIsNone(self.check("https://docs.example/a/", ""))


class Resolved(unittest.TestCase):
    def test_a_setting_renders_over_only_the_names_it_uses(self):
        raw = {"a": 2, "b": "{{ a * 3 }}", "c": ["{{ b }}", "x"], "d": {"k": "{{ b }}"},
               "host": "{{ ansible_hostname }}", "loop": "{{ loop }}", "secret": object(), "e": "{{ secret }}"}  # fmt: skip
        self.assertEqual(sf.resolved("b", raw), 6)
        self.assertEqual(sf.resolved("c", raw), [6, "x"])
        self.assertEqual(sf.resolved("d", raw), {"k": 6})
        for name in ("host", "loop", "secret", "e", "unset"):
            self.assertIsNone(sf.resolved(name, raw), name)


class Slurm(unittest.TestCase):
    def test_preemption_times_follow_killwait_from_the_sites_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = site(tmp)
            (config / "files/slurm").mkdir(parents=True)
            (config / "files/slurm/slurm.conf").write_text("ClusterName=c\nKillWait=45\nResumeTimeout=600\n")
            got = sf.slurm_values(config)
        self.assertEqual(
            got,
            {"kill_wait_seconds": 45, "preempt_grace_seconds": 45, "sigkill_after_seconds": 90,
             "resume_timeout_seconds": 600},
        )  # fmt: skip
        self.assertNotIn("resume_timeout_seconds", sf.PUBLIC["slurm"], "an admin timing")

    def test_without_a_site_template_the_roles(self):
        self.assertIn("kill_wait_seconds", sf.slurm_values(None))


class Cluster(unittest.TestCase):
    def test_nodes_and_partitions_from_the_literal_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = site(tmp)
            (config / "files/slurm").mkdir(parents=True)
            (config / "files/slurm/slurm.conf").write_text(
                "NodeName=n1 NodeAddr=192.0.2.1 CPUs=8 RealMemory=1000 Gres=gpu:t:2{{ tmp_disk('n1') }}\n"
                "PartitionName=p Nodes=n1 MaxTime=1-00:00:00 State=UP\n"
            )
            got = sf.cluster(config)
        self.assertEqual(got["nodes"], [{"name": "n1", "cpus": 8, "memory_mb": 1000, "gres": "gpu:t:2"}])
        self.assertEqual(got["partitions"], [{"name": "p", "nodes": "n1", "max_time": "1-00:00:00"}])
        self.assertNotIn("192.0.2.1", json.dumps(got), "no address")
        self.assertNotIn("cluster", sf.PUBLIC, "infrastructure stays private")

    def test_the_portal_reads_the_same_filter(self):
        # open_ondemand_overview: "{{ lookup('file', slurm_conf_template) | slurm_conf_cluster }}"
        filters = sf._slurm_conf.FilterModule().filters()
        self.assertIs(filters["slurm_conf_cluster"], sf._slurm_conf.slurm_conf_cluster)


class Quota(unittest.TestCase):
    def test_limited_filesystems_with_derived_files_and_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = site(tmp, "disk_quota_inodes_per_tib: 1000\n",
                          {"h1": 'disk_quota_filesystems: {"/": {}, "/data": {block_hard: 2T}}\n',
                           "h2": 'disk_quota_filesystems: {"/fast": {block_hard: 512G, inode_hard: 7}}\n'})  # fmt: skip
            got = sf.facts(config)["quota"]
        self.assertEqual(
            got["/data"],
            {"block_hard_tib": 2.0, "block_warn_tib": 1.8, "inode_hard": 2000, "inode_warn": 1800},
        )
        self.assertEqual(got["/fast"]["inode_hard"], 7, "an inode_hard of its own wins")
        self.assertNotIn("/", got, "a filesystem that only counts has no limits to show")
        self.assertEqual(sf.public({"quota": {"/data": dict(got["/data"], extra=1)}})["quota"]["/data"].keys(),
                         sf.QUOTA_FIELDS)  # fmt: skip


class Dates(unittest.TestCase):
    def test_the_schedule_passes_only_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = site(tmp)
            (config / "schedule.yml").write_text("notice_day: 2030-01-02\n")
            self.assertEqual(sf.facts(config)["dates"], {"notice_day": "2030-01-02"})
            (config / "schedule.yml").write_text("notice_day: soon\n")
            with self.assertRaises(SystemExit):
                sf.facts(config)
        self.assertEqual(
            sf.public({"dates": {"a": "2030-01-02", "b": "10.0.0.1"}})["dates"], {"a": "2030-01-02"}
        )


class ServicesAndPaths(unittest.TestCase):
    """What the pages say runs, from the switches that run it; never an address."""

    def test_the_portal_its_menu_and_the_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = site(tmp)
            first = sf.facts(config)["services"]
            boards = first.pop("dashboards")
            self.assertEqual(first, {"portal": False, "overview": "", "menu": [], "dashboard": True, "bot": False,
                              "job_notices": True, "portal_groups": [], "portal_files": []},
                             "the defaults: no portal, monitoring and job notices on")  # fmt: skip
            # Every dashboard the roles ship, each by the uid and title its file holds.
            self.assertIn({"uid": "gpu-nodes", "title": "GPU / Nodes"}, boards)
            self.assertEqual(
                len(boards), sum(len(f) for f in sf.settings(config)["grafana_dashboard_sets"].values())
            )
            (config / "group_vars" / "slurm-cluster.yml").write_text(
                "slurm_install_open_ondemand: true\nslurm_enable_cluster_bot: true\nslurm_enable_job_notices: false\n"
                "open_ondemand_servername: portal.example.org\n"
                "open_ondemand_overview: \"{{ lookup('file', 'x') }}\"\n"
                "open_ondemand_overview_title: Overview\n"
                "open_ondemand_proxied_services:\n"
                '  - {path: "{{ p }}", title: Grafana}\n'
                "  - {path: /hidden/}\n"
                "slurm_enable_monitoring: false\n"
                "open_ondemand_nav_groups: [files, jobs]\n"
            )
            every = sf.facts(config)
        self.assertEqual(
            every["services"],
            {
                "portal": True,
                "overview": "Overview",
                "menu": ["Grafana"],
                "dashboard": False,
                "bot": True,
                "job_notices": False,
                "portal_groups": ["files", "jobs"],
                "portal_files": ["/home"],
                "dashboards": [],
            },
        )
        self.assertNotIn("portal.example.org", json.dumps(sf.public(every)))

    def test_the_shared_trees(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = sf.facts(site(tmp, "sm_prefix: /opt/sw\n"))["paths"]
        self.assertEqual(out["software"], "/opt/sw")
        self.assertEqual(out["scratch"], "/scratch", "the role default")
        self.assertTrue(out["shared"].startswith("/"), "a template over the system account's home")
        self.assertIn("datasets", out["shared_data"])


class InteractiveTeamQos(unittest.TestCase):
    def test_whether_gpu_sessions_must_name_a_team_qos_and_never_the_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = site(tmp)
            self.assertIs(
                sf.facts(config)["policy"]["interactive_team_qos"], False, "the role default: no rule"
            )
            (config / "group_vars" / "slurm-cluster.yml").write_text(
                'slurm_job_submit_policy: {interactive_gpu_qos: "^team_"}\n'
            )
            every = sf.facts(config)
        self.assertIs(every["policy"]["interactive_team_qos"], True)
        self.assertNotIn("^team_", json.dumps(sf.public(every)))


class SubmitRules(unittest.TestCase):
    def test_the_job_rules_the_user_pages_state_from_the_submit_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = site(tmp)
            policy = sf.public(sf.facts(config))["policy"]
            self.assertEqual(policy["gpus_per_node"], [], "the role default: any count")
            for rule in (*sf.SUBMIT_RULES, "gpu_type_defaulted"):
                self.assertIs(policy[rule], False, rule)
            (config / "group_vars" / "slurm-cluster.yml").write_text(
                "slurm_job_submit_policy: {gpus_per_node: ['1', 2, 4, 8], default_gpu_type: gtype,"
                " strict_gpu_type: true, force_gpu_partition: true, gpu_jobs_use_defaults: true,"
                " gpu_nodes_shared: true}\n"
            )
            policy = sf.public(sf.facts(config))["policy"]
        self.assertEqual(policy["gpus_per_node"], [1, 2, 4, 8])
        for rule in (*sf.SUBMIT_RULES, "gpu_type_defaulted"):
            self.assertIs(policy[rule], True, rule)
        self.assertNotIn("gtype", json.dumps(policy), "a GPU type is the site's, never public")


class SubmitLimits(unittest.TestCase):
    """What the troubleshooting page says refuses a submission: the queued-job cap of the
    accounting file, as Slurm inherits it, and how the submit holds' refusals begin."""

    ACCOUNTING = (
        "accounts:\n  root: {members: [root]}\n"
        "  lab: {parent: root, max_submit_jobs: %s}\n"
        "  team: {parent: lab, members: [alice, %s]}\n"
    )

    def cap(self, lab, member="bob"):
        with tempfile.TemporaryDirectory() as tmp:
            config = site(tmp, 'slurm_accounting_file: "{{ inventory_dir }}/files/accounting.yml"\n')
            (config / "files").mkdir()
            (config / "files" / "accounting.yml").write_text(self.ACCOUNTING % (lab, member))
            return sf.public(sf.facts(config))["policy"]["max_submit_jobs"]

    def test_the_cap_every_account_inherits(self):
        self.assertEqual(self.cap(500), 500, "team inherits lab's")
        self.assertEqual(self.cap(500, "{user: bob, max_submit_jobs: 500}"), 500)

    def test_no_one_number_when_they_differ_or_none_is_set(self):
        self.assertIsNone(self.cap(500, "{user: bob, max_submit_jobs: 50}"))
        self.assertIsNone(self.cap("null"))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(sf.facts(site(tmp))["policy"]["max_submit_jobs"], "accounting not managed")

    def test_the_refusals_begin_as_the_holds_write_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = sf.public(sf.facts(site(tmp)))
        self.assertTrue(sf.submit_holds.ends_holds({"a": {"ends": "2000-01-01", "login_expires": "x"}}, 10**10)["a"]
                        .startswith(out["messages"]["hold_ended"]))  # fmt: skip
        self.assertEqual(out["messages"]["hold_disk"], "Your disk use on")
        self.assertIs(out["policy"]["submit_holds"], False, "the role default")


class Storage(unittest.TestCase):
    def test_each_measured_filesystems_speeds_and_nothing_else(self):
        model = "{rbps: 1200000000, wbps: 900000000, rrandiops: 1600, wrandiops: 500, rseqiops: 1}"
        with tempfile.TemporaryDirectory() as tmp:
            text = f"io_fairness_models:\n  /home: {{measured: x, command: y, model: {model}}}\n"
            out = sf.public(sf.facts(site(tmp, text)))["storage"]
        self.assertEqual(out, {"/home": {"read_bps": 1200000000, "write_bps": 900000000,
                                         "read_iops": 1600, "write_iops": 500}})  # fmt: skip


class LoginLimits(unittest.TestCase):
    def test_the_login_groups_user_slice_limits_as_people_read_them(self):
        """The login nodes' per-person limits (cluster_tools_user_slice_limits in
        group_vars/slurm-login.yml): CPUQuota in CPUs, MemoryMax in GiB, TasksMax."""
        with tempfile.TemporaryDirectory() as tmp:
            config = site(tmp)
            (config / "group_vars" / "slurm-login.yml").write_text(
                'cluster_tools_user_slice_limits: {CPUQuota: "800%", MemoryMax: 64G, TasksMax: 4096, IOAccounting: "yes"}\n'
            )
            self.assertEqual(sf.facts(config)["login"], {"cpus": 8, "memory_gib": 64, "tasks": 4096})
            (config / "group_vars" / "slurm-login.yml").write_text(
                "cluster_tools_user_slice_limits: {CPUQuota: 250%, MemoryMax: 512M}\n"
            )
            self.assertEqual(
                sf.facts(config)["login"], {"cpus": 2.5, "memory_gib": 0.5}, "an unset limit is left out"
            )

    def test_the_directories_each_login_has_its_own_of(self):
        """roles/login_private_dirs' directories, public, only while its switch is on."""
        with tempfile.TemporaryDirectory() as tmp:
            config = site(tmp)
            (config / "group_vars" / "slurm-cluster.yml").write_text(
                "slurm_enable_login_private_dirs: true\n"
            )
            every = sf.facts(config)
            self.assertEqual(every["login"], {"private_dirs": ["/tmp", "/var/tmp"]})
            self.assertEqual(sf.public(every)["login"], {"private_dirs": ["/tmp", "/var/tmp"]})

    def test_no_login_limits_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sf.facts(site(tmp))["login"], {})


class Threads(unittest.TestCase):
    def test_the_knobs_are_public_with_kind_reader_and_default(self):
        """roles/user_environment_defaults' table; the site's own replaces it."""
        with tempfile.TemporaryDirectory() as tmp:
            every = sf.facts(site(tmp))
            omp = next(v for v in every["threads"]["variables"] if v["name"] == "OMP_NUM_THREADS")
            self.assertEqual((omp["kind"], omp["value"]), ("threads", "{n}"))
            self.assertTrue(omp["reader"] and omp["default"])
            self.assertEqual(sf.public(every)["threads"], every["threads"])
            config = site(tmp + "/b", all_yml="user_environment_cpu_knobs: [{name: MAX_JOBS, kind: build}]\n")
            self.assertEqual(
                sf.facts(config)["threads"],
                {
                    "variables": [
                        {"name": "MAX_JOBS", "kind": "build", "value": "{n}", "reader": "", "default": ""}
                    ]
                },
            )


class PublicAllowList(unittest.TestCase):
    def test_only_allowed_keys_and_fields_reach_the_public_file(self):
        every = {
            "commands": {"sq": {"summary": "s", "usage": "sq", "options": [], "internal": 1}},
            "policy": {
                "stale_days": 60,
                "grace_days": 14,
                "warn_fraction": 0.9,
                "idle_hours": 2,
                "cancel_after_hours": 6,
                "cancel_warn_hours": 1,
                "idle_notify": ["mention"],
                "leave_grace_days": 60,
                "interactive_team_qos": True,
                "submit_holds_disk": True,
                "extra": 1,
            },  # fmt: skip
            "chat": {"channels": {"admin": "#x"}, "admin_group": "@ops", "admin_group_id": "S0"},
            "bot": {"command": "/c", "subcommands": [], "token": "x"},
            "reports": {"usage": "hourly", "weekly": "Mon", "disk": "daily", "host": "h"},
            "schedules": {"scratch_purge": "*-*-* 03:30:00", "host": "h"},
            "quotas": {"hosts": {"n": {}}},
            "anything_new": {"a": 1},
        }
        out = sf.public(every)
        self.assertEqual(set(out), set(sf.PUBLIC))
        self.assertEqual(set(out["commands"]["sq"]), {"summary", "usage", "options"})
        self.assertEqual(
            set(out["policy"]),
            {
                "stale_days",
                "grace_days",
                "warn_fraction",
                "idle_hours",
                "cancel_after_hours",
                "cancel_warn_hours",
                "idle_notify",
                "leave_grace_days",
                "interactive_team_qos",
                "submit_holds_disk",
            },
        )
        self.assertEqual(out["chat"], {"channels": {"admin": "#x"}, "admin_group": "@ops"})
        self.assertEqual(set(out["bot"]), {"command", "subcommands"})
        self.assertEqual(set(out["reports"]), {"usage", "weekly", "disk"})
        self.assertEqual(out["schedules"], {"scratch_purge": "*-*-* 03:30:00"})
        self.assertEqual(set(out["commands"]["sq"]) - {"summary_ko"}, {"summary", "usage", "options"})

    def test_the_allow_list_holds_nothing_private(self):
        self.assertFalse({"slack", "quotas"} & set(sf.PUBLIC), "limits stay in the private file")
        self.assertEqual(sf.PUBLIC["chat"], {"channels", "admin_group"}, "the group's ID stays private")


class Check(unittest.TestCase):
    def test_check_names_a_stale_file_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = site(tmp)
            out = Path(tmp) / "pub.json"
            self.assertEqual(sf.main(["--config", str(config), "--public", str(out)]), 0)
            self.assertEqual(sf.main(["--config", str(config), "--public", str(out), "--check"]), 0)
            out.write_text("{}\n")
            self.assertEqual(sf.main(["--config", str(config), "--public", str(out), "--check"]), 1)
            self.assertEqual(out.read_text(), "{}\n")


if __name__ == "__main__":
    unittest.main()
