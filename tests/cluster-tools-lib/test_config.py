"""clustertools.config, and that every role shipping a tool config holds to it.

A tool's config is checked whole by the tool's load_config, at deploy time by the role
(validate) and by every later package (the records), so a package that would refuse a
config fails a deploy naming the playbook that rewrites it, not the tool's next run.
"""

import importlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "roles/cluster_tools_lib/files"))
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from clustertools import config  # noqa: E402
from repo_scan import ansible_files  # noqa: E402

ROLES = ROOT / "roles"


class Require(unittest.TestCase):
    def write(self, d, cfg):
        path = Path(d, "c.json")
        path.write_text(json.dumps(cfg))
        return path

    def test_keys_types_and_undeclared_keys(self):
        keys, optional = {"a": int, "b": (list, type(None))}, {"c": bool}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                config.require(self.write(d, {"a": 1, "b": None}), keys, optional), {"a": 1, "b": None}
            )
            for cfg, word in (
                ({"b": []}, "a is missing"),
                ({"a": True, "b": []}, "a is missing or not a int"),  # true is no number
                ({"a": 1, "b": [], "c": 1}, "c is not a bool"),
                ({"a": 1, "b": [], "old": 1}, "old: not a setting"),
            ):
                with self.subTest(cfg=cfg), self.assertRaisesRegex(config.ConfigError, word):
                    config.require(self.write(d, cfg), keys, optional)

    def test_unreadable_and_not_an_object(self):
        with tempfile.TemporaryDirectory() as d:
            for text, word in (("[1]", "not a JSON object"), ("{", "not JSON")):
                Path(d, "c.json").write_text(text)
                with self.subTest(text=text), self.assertRaisesRegex(config.ConfigError, word):
                    config.read(Path(d, "c.json"))
            with self.assertRaisesRegex(config.ConfigError, "cannot be read"):
                config.read(Path(d, "absent.json"))

    def test_a_dash_reads_standard_input(self):
        with mock.patch.object(sys, "stdin", io.StringIO('{"a": 1}')):
            self.assertEqual(config.read("-"), {"a": 1})


class Installed(unittest.TestCase):
    """Every recorded config against this package: each misfit names its playbook."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.records = self.base / "configs.d"
        self.records.mkdir()

    def record(self, role, cfg, module="clustertools.egress_metrics"):
        path = self.base / f"{role}.cfg.json"
        if cfg is not None:
            path.write_text(json.dumps(cfg))
        (self.records / f"{role}.json").write_text(
            json.dumps(
                {"role": role, "module": module, "config": str(path), "playbook": f"playbooks/{role}.yml"}
            )
        )

    def test_a_misfit_names_the_playbook_and_skipped_or_absent_ones_pass(self):
        good = {"table": "t", "inbound_table": "i", "user_uids": [], "tunnel_programs": []}
        self.record("fits", good)
        self.record("old", {"table": "t"})
        self.record("deploying", {"table": "t"})
        self.record("removed", None)
        problems = config.installed(self.records, skip=["deploying"])
        self.assertEqual(len(problems), 1)
        self.assertIn("inbound_table is missing", problems[0])
        self.assertTrue(problems[0].endswith("; rerun playbooks/old.yml"))
        self.assertEqual(config.main(["installed", "--dir", str(self.records), "--skip", "deploying"]), 1)
        self.assertEqual(
            config.main(["installed", "--dir", str(self.records), "--skip=old", "--skip=deploying"]), 0
        )

    def test_a_damaged_record_is_reported(self):
        (self.records / "bad.json").write_text("{")
        self.assertIn("a damaged record", config.installed(self.records)[0])


def role_tasks(role):
    """Every task in the role's task files (tracked, .yml or .yaml)."""
    base = ROLES / role / "tasks"
    for path in ansible_files():
        if base in path.parents:
            yield from yaml.safe_load(path.read_text()) or []


def flat(tasks):
    for task in tasks:
        yield task
        yield from flat(task.get("block", []))


def recorded():
    """{role: its record's vars} for every role that records a tool config."""
    roles = {path.relative_to(ROLES).parts[0] for path in ansible_files() if ROLES in path.parents}
    out = {}
    for role in sorted(roles - {"cluster_tools_lib"}):
        for task in flat(role_tasks(role)):
            include = task.get("ansible.builtin.include_role") or {}
            if include.get("name") == "cluster_tools_lib" and include.get("tasks_from") == "config":
                out[role] = task["vars"]["cluster_tools_lib_config"]
    return out


def written_keys(role, record):
    """The keys of the config file the role writes: the task whose dest is the record's
    file, from its template (a JSON object's "key": lines), its inline dict, or the vars
    mapping it dumps."""
    for task in flat(role_tasks(role)):
        module = task.get("ansible.builtin.copy") or task.get("ansible.builtin.template") or {}
        if module.get("dest") != record["file"]:
            continue
        if "src" in module:
            text = (ROLES / role / "templates" / module["src"]).read_text()
            return set(re.findall(r'^\s*"(\w+)":', text, re.M)) - {"_comment"}
        content = module["content"]
        named = re.fullmatch(r"\{\{\s*(\w+)\s*\|\s*to_nice_json\s*\}\}\n?", content.strip() + "\n")
        if named:
            values = yaml.safe_load((ROLES / role / "vars/main.yml").read_text())
            return set(values[named.group(1)])
        return set(re.findall(r"'(\w+)':", content))
    raise AssertionError(f"{role}: no task writes {record['file']}")


class Roles(unittest.TestCase):
    """Each role that ships a tool config validates it with its tool, records it, removes
    the record with the component and writes exactly the keys the tool takes."""

    def test_the_tools_that_read_a_role_written_config_are_all_recorded(self):
        self.assertEqual(
            sorted(recorded()),
            ["container_storage_gc", "disk_quota", "egress_policy", "job_notice", "scratch_alloc",
             "slurm_backup", "slurm_usage_report", "submit_holds"],
        )  # fmt: skip

    def test_each_role_validates_records_and_removes_its_record(self):
        for role, record in recorded().items():
            with self.subTest(role=role):
                self.assertEqual(record["role"], role)
                self.assertTrue((ROOT / record["playbook"]).is_file(), record["playbook"])
                tasks = (ROLES / role / "tasks/present.yml").read_text()
                self.assertIn(f"{{{{ cluster_tools_lib_config_validate }}}} {record['module']} %s", tasks)
                items = (ROLES / role / "vars/main.yml").read_text()
                self.assertRegex(items, rf"cluster_tools_lib_config_records \}}?\}}?[^\n]*/{role}\.json")

    def test_each_role_writes_exactly_the_keys_its_tool_takes(self):
        # A key the tool needs and the role lacks fails every deploy (validate); one the
        # role writes and the tool does not take is refused too, a setting doing nothing.
        for role, record in recorded().items():
            with self.subTest(role=role):
                tool = importlib.import_module(record["module"])
                written = written_keys(role, record)
                self.assertLessEqual(set(tool.CONFIG_KEYS), written)
                self.assertLessEqual(
                    written, set(tool.CONFIG_KEYS) | set(getattr(tool, "CONFIG_OPTIONAL", {}))
                )


if __name__ == "__main__":
    unittest.main()
