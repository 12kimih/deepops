"""The users' environment (roles/user_environment_defaults): one list, rendered into the
login's profile.d files (roles/cluster_tools), the job's task prolog part (roles/slurm)
and the docs' table (scripts/docs/site_facts.py). Each is rendered from the role
defaults and run; a name of the list set anywhere else in the code fails."""

import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402
from repo_scan import tracked  # noqa: E402

user_environment = in_tree.load("plugins/filter/user_environment.py").user_environment
spec = importlib.util.spec_from_file_location("site_facts", ROOT / "scripts/docs/site_facts.py")
site_facts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_facts)

DEFAULTS = yaml.safe_load((ROOT / "roles/user_environment_defaults/defaults/main.yml").read_text())
# The defaults as a host renders them with the role defaults alone (a derived one, such as
# SHARED_HF_HUB_CACHE without a shared cache, empty and so not set).
_ROLE_SETTINGS = site_facts.settings(None)
RENDERED_DEFAULTS = {
    k: site_facts._resolve(v, _ROLE_SETTINGS) for k, v in DEFAULTS["user_environment_defaults"].items()
}
assert None not in RENDERED_DEFAULTS.values(), RENDERED_DEFAULTS
PROFILE_SH = "roles/cluster_tools/templates/profile.sh.j2"
PROFILE_CSH = "roles/cluster_tools/templates/profile.csh.j2"
PROLOG = "roles/slurm/templates/etc/slurm/task-prolog.d/60-user-environment"
# The only files that may set a name of the list: the list, its function, its renderers.
OWNERS = {
    "roles/user_environment_defaults/defaults/main.yml",
    "plugins/filter/user_environment.py",
    PROFILE_SH,
    PROFILE_CSH,
    PROLOG,
}


def render(template, entries):
    """TEMPLATE as Ansible renders it (trim_blocks), with ENTRIES."""
    env = Environment(  # noqa: S701 (shell, not HTML)
        loader=FileSystemLoader(str(ROOT)),
        undefined=StrictUndefined,
        trim_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["quote"] = shlex.quote
    profile = [e for e in entries if e["login"]]
    return env.get_template(template).render(
        ansible_managed="test",
        cluster_tools_profile_name="z10-test",
        cluster_tools_profile_entries=profile,
        user_environment_entries=entries,
    )


def run(shell, script, env, *argv):
    with tempfile.NamedTemporaryFile("w", suffix=".sh") as f:
        f.write(script)
        f.flush()
        return subprocess.run(
            [shell, *argv, f.name], env=env, capture_output=True, text=True, check=True
        ).stdout


class List(unittest.TestCase):
    def test_site_entry_replaces_and_null_drops(self):
        got = user_environment(
            {"A": "/a", "B": {"login": "~/b", "job": "/tmp/b"}, "C": "x"}, {"A": {"job": "/j"}, "C": None}
        )
        self.assertEqual(
            got, [{"name": "A", "login": "", "job": "/j"}, {"name": "B", "login": "~/b", "job": "/tmp/b"}]
        )

    def test_job_equal_to_login_is_no_job_value(self):
        self.assertEqual(user_environment({"A": {"login": "/a", "job": "/a"}})[0]["job"], "")

    def test_bad_name_or_key_refused(self):
        with self.assertRaises(ValueError):
            user_environment({"A-B": "/x"})
        with self.assertRaises(ValueError):
            user_environment({"A": {"logn": "/x"}})

    def test_a_library_cache_on_shared_storage_is_refused(self):
        # huggingface_hub writes into HF_HUB_CACHE for any file not yet in it: a shared,
        # writer-owned default fails those downloads for everyone else.
        for site in ({"HF_HUB_CACHE": "/srv/models/hub"}, {"HF_HOME": "/home/shared/hf"},
                     {"PIP_CACHE_DIR": {"login": "/tmp/pip"}}, {"UV_CACHE_DIR": {"login": "~/u", "job": "/scratch/uv"}}):  # fmt: skip
            with self.subTest(site=site), self.assertRaisesRegex(ValueError, "never shared storage"):
                user_environment(RENDERED_DEFAULTS, site)
        got = user_environment(
            {}, {"HF_HUB_CACHE": "~/.cache/hub", "PIP_CACHE_DIR": {"login": "~/p", "job": "/tmp/p"}}
        )
        self.assertEqual([e["name"] for e in got], ["HF_HUB_CACHE", "PIP_CACHE_DIR"], "per user: allowed")
        self.assertEqual(
            user_environment({}, {"SHARED_HF_HUB_CACHE": "/srv/models/hub"})[0]["login"], "/srv/models/hub"
        )


class Rendered(unittest.TestCase):
    entries = user_environment(RENDERED_DEFAULTS)

    def login_env(self, shell, script, **preset):
        names = [e["name"] for e in self.entries if e["login"]]
        body = script + "".join(f"printf '%s=%s\\n' {n} \"${n}\"\n" for n in names)
        out = run(
            shell,
            body,
            {"PATH": os.environ["PATH"], "HOME": "/h", **preset},
            *(["-f"] if shell != "sh" else []),
        )
        return dict(line.split("=", 1) for line in out.splitlines())

    def test_profile_sh_gives_every_login_value_and_keeps_the_users(self):
        got = self.login_env("sh", render(PROFILE_SH, self.entries), UV_CACHE_DIR="/mine")
        want = {e["name"]: e["login"].replace("~/", "/h/", 1) for e in self.entries if e["login"]}
        want["UV_CACHE_DIR"] = "/mine"
        self.assertEqual(got, want)

    @unittest.skipUnless(shutil.which("tcsh") or shutil.which("csh"), "csh not installed")
    def test_profile_csh_gives_the_same(self):
        shell = shutil.which("tcsh") or shutil.which("csh")
        sh = self.login_env("sh", render(PROFILE_SH, self.entries))
        self.assertEqual(self.login_env(shell, render(PROFILE_CSH, self.entries)), sh)

    def prolog(self, **preset):
        out = run("bash", render(PROLOG, self.entries), {"PATH": os.environ["PATH"], **preset})
        return out.splitlines()

    def test_prolog_sets_job_values_unless_the_user_chose_one(self):
        home = os.path.expanduser("~" + __import__("pwd").getpwuid(os.getuid()).pw_name)
        jobs = {e["name"]: e for e in self.entries if e["job"]}
        self.assertTrue(jobs, "the defaults give a job value")
        want = sorted(f"export {n}={e['job']}" for n, e in jobs.items())
        self.assertEqual(sorted(self.prolog()), want, "unset")
        inherited = {n: e["login"].replace("~/", home + "/", 1) for n, e in jobs.items()}
        self.assertEqual(sorted(self.prolog(**inherited)), want, "the login value, inherited")
        self.assertEqual(self.prolog(**{n: "/mine" for n in jobs}), [], "the user's own")

    def test_prolog_lines_pass_the_task_prologs_filter(self):
        line = re.compile(r"^(export [A-Za-z_][A-Za-z0-9_]*=|unset [A-Za-z_][A-Za-z0-9_]*$)")
        self.assertTrue(all(line.match(x) for x in self.prolog()))


class Docs(unittest.TestCase):
    def test_the_docs_table_is_the_same_list(self):
        every = site_facts.facts(None)
        entries = user_environment(RENDERED_DEFAULTS)
        self.assertEqual(every["environment"], entries)
        public = site_facts.public(every)["environment"]
        self.assertEqual(
            public, [e for e in entries if all(v == "" or v[0] in "/~" for v in (e["login"], e["job"]))]
        )

    def test_a_value_that_is_not_a_path_stays_private(self):
        every = {"environment": user_environment({"P": "/p", "F": "%9P %5t"})}
        self.assertEqual([e["name"] for e in site_facts.public(every)["environment"]], ["P"])

    def environment(self, all_yml):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config"
            (config / "group_vars").mkdir(parents=True)
            (config / "group_vars" / "all.yml").write_text(all_yml)
            return {e["name"]: e for e in site_facts.environment(config)}

    def test_a_derived_setting_resolves_as_ansible_would(self):
        got = self.environment(
            "model_cache_mount: /srv\nmodel_cache_root: /srv/models\nuser_environment:\n"
            '  UV_CACHE_DIR: null\n  X: "{{ undefined_here }}"\n  G: "{{ groups.all }}"\n'
        )
        self.assertEqual(got["SHARED_HF_HUB_CACHE"]["login"], "/srv/models/hub", "through model_cache_hub")
        self.assertNotIn("UV_CACHE_DIR", got, "null drops a default")
        self.assertNotIn("X", got, "a template left unresolved is left out")
        self.assertNotIn("G", got, "what only a host knows is left out")
        self.assertEqual(self.environment("model_cache_mount: /srv\n")["SHARED_HF_HUB_CACHE"]["login"],
                         "/srv/hf-cache/hub", "the root's default")  # fmt: skip
        self.assertNotIn("SHARED_HF_HUB_CACHE", self.environment("x: 1\n"), "no shared cache: not set")


class NoSecondWriter(unittest.TestCase):
    def test_no_name_of_the_list_is_set_anywhere_else(self):
        example = (ROOT / "config.example/group_vars/slurm-cluster.yml").read_text()
        block = example.split("#user_environment:", 1)[1].split("\n\n", 1)[0]
        names = set(DEFAULTS["user_environment_defaults"]) | set(
            re.findall(r"^#  ([A-Z][A-Z0-9_]*):", block, re.M)
        )
        self.assertIn("SHARED_HF_HUB_CACHE", names)
        alt = "|".join(sorted(names))
        setting = re.compile(rf"(?:\bexport|\bsetenv|\benv)\s+(?:{alt})\b|^\s*(?:{alt})\s*[:=]", re.M)
        offenders = []
        files = tracked("roles", "playbooks", "scripts", "plugins", "virtual", "src")
        self.assertGreater(len(files), 100)
        for path in files:
            rel = str(path.relative_to(ROOT))
            if rel in OWNERS or not path.is_file() or "/molecule/" in rel or "/tests/" in rel:
                continue
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            offenders += [f"{rel}:{text[: m.start()].count(chr(10)) + 1}" for m in setting.finditer(text)]
        self.assertEqual(
            offenders, [], "set these through user_environment (roles/user_environment_defaults)"
        )


if __name__ == "__main__":
    unittest.main()
