"""A variable several roles need is defined once.

A copy per role must be kept equal by hand, and a role whose fallback drifted
quietly disagrees with the others wherever config/ is silent; a fallback written as
`| default(<value>)` (or its alias `| d(...)`) is such a copy too, a true/false one
included, and in a play that does not load the owning role it always wins. So a
shared value lives in one defaults-only role (prometheus_paths, slurm_paths,
deepops_paths) that the roles reading it depend on, and this test fails on a second
definition, in defaults or vars, or a literal fallback of a defined variable.

What a deploy includes is read by plays, not roles: an import's `when`, a play's own
tasks. Those switches live in deploy_defaults, which every such play lists in its
roles (an import's `when` is evaluated in the imported plays, where it sees the
role's defaults). A setting config.example shows but no role defines has no default
at all, so a fallback there is a second, unchecked one.
"""

import re
import sys
import unittest
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import Scan, role_var_files, tracked  # noqa: E402

# Deliberate per-role overrides: the DGX role points cachefilesd at its RAID.
INTENTIONAL = {
    ("cachefilesd_cache_dir", "nvidia-dgx"),
    ("cachefilesd_cache_tag", "nvidia-dgx"),
}
# Kubernetes plugin roles each run standalone against a cluster, by design.
STANDALONE = {"k8s_gpu_mig_strategy"}
# runai finds a GPU Operator release some other play installed; it does not load
# that role, so it names the release the same way as a fallback.
FALLBACK_OK = {("roles/runai/tasks/fractioning_preflight.yml", "gpu_operator_release_name")}
FALLBACK = re.compile(r"([a-z_][a-z0-9_]*)\s*\|\s*(?:default|d)\(\s*([^)]*)\)")
EMPTY = {"omit", "none", "None", "", "''", '""', "[]", "{}"}


def role_defaults():
    found = defaultdict(dict)
    for path in role_var_files():
        role = path.relative_to(ROOT).parts[1]
        for name, value in (yaml.safe_load(path.read_text()) or {}).items():
            # Leading underscore marks a role-private helper, free to differ.
            if not name.startswith("_") and (name, role) not in INTENTIONAL:
                found[name][role] = value
    return found


class SharedRoleDefaultTests(unittest.TestCase):
    def test_a_shared_variable_is_defined_once(self):
        twice = {n: sorted(r) for n, r in role_defaults().items() if len(r) > 1 and n not in STANDALONE}
        self.assertEqual(twice, {}, "define it once, in a shared defaults role the readers depend on")
        self.assertGreater(len(role_defaults()), 500, "the scan found too few role variables")

    def test_no_literal_fallback_restates_a_defined_variable(self):
        owners = role_defaults()
        scan, offenders = Scan(FALLBACK_OK, minimum=200), []
        for path in map(
            scan.seen, tracked("roles/*.y*ml", "roles/**/*.y*ml", "roles/**/*.j2", "playbooks/**")
        ):
            name = path.relative_to(ROOT).as_posix()
            if (
                name.startswith("roles/galaxy/")
                or "/molecule/" in name
                or path.suffix not in (".yml", ".yaml", ".j2")
            ):
                continue
            role = name.split("/")[1] if name.startswith("roles/") else None
            for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                for var, arg in FALLBACK.findall(line):
                    literal = arg.split(",")[0].strip()
                    if var in owners and literal not in EMPTY and role not in owners[var]:
                        if not scan.excuse((name, var)):
                            offenders.append(f"{name}:{number}: {var} | default({arg})")
        self.assertEqual(offenders, [], "depend on the role that defines it instead of restating its value")
        scan.check(self)

    def test_the_owning_role_never_falls_back(self):
        # Inside its role a variable is always defined, so a fallback never applies and
        # only misleads (slurm_autodetect_nvml read `| default(false)` over its true).
        # `default(x, true)`, which also replaces an empty value, is a normalisation.
        owners = role_defaults()
        scan, offenders = Scan(minimum=200), []
        for path in map(scan.seen, ansible_sources()):
            name = path.relative_to(ROOT).as_posix()
            role = name.split("/")[1] if name.startswith("roles/") else None
            for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                for var, arg in FALLBACK.findall(line):
                    if var in owners and role in owners[var] and not re.search(r",\s*true\s*$", arg):
                        offenders.append(f"{name}:{number}: {var} | default({arg})")
        self.assertEqual(offenders, [], "the role defines it: read it bare")
        scan.check(self)

    def test_a_setting_without_an_owner_has_no_fallback(self):
        owners, shown = role_defaults(), example_settings()
        scan, offenders = Scan(minimum=200), []
        for path in map(scan.seen, ansible_sources()):
            name = path.relative_to(ROOT).as_posix()
            for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                for var, arg in FALLBACK.findall(line):
                    if var in shown and var not in owners and arg.split(",")[0].strip() not in EMPTY:
                        offenders.append(f"{name}:{number}: {var} | default({arg})")
        self.assertEqual(offenders, [], "define it in the role that reads it, or in deploy_defaults")
        scan.check(self)

    def test_a_play_reading_a_deploy_switch_loads_deploy_defaults(self):
        switches = set(yaml.safe_load((ROOT / "roles/deploy_defaults/defaults/main.yml").read_text()))
        word = re.compile(r"\b(" + "|".join(sorted(switches)) + r")\b")
        scan, missing = Scan(minimum=80), []

        def check(path, play, why):
            if "deploy_defaults" not in role_closure(play.get("roles") or []):
                missing.append(
                    f"{path.relative_to(ROOT)}: play {play.get('name') or play.get('hosts')} ({why})"
                )

        def imported(path, why):
            for play in yaml.safe_load(path.read_text()) or []:
                if "import_playbook" in play:
                    imported((path.parent / play["import_playbook"]).resolve(), why)
                else:
                    check(path, play, why)

        for path in map(scan.seen, tracked("playbooks/*.yml", "playbooks/**/*.yml")):
            data = yaml.safe_load(path.read_text())
            for play in data if isinstance(data, list) else []:
                if not isinstance(play, dict):
                    continue
                if "import_playbook" in play:
                    found = word.findall(str(play.get("when", "")))
                    if found:
                        imported(
                            (path.parent / play["import_playbook"]).resolve(), f"imported when {found[0]}"
                        )
                else:
                    found = word.findall(yaml.safe_dump({k: v for k, v in play.items() if k != "hosts"}))
                    if found:
                        check(path, play, f"reads {found[0]}")
        self.assertEqual(missing, [], "list deploy_defaults first in the play's roles")
        scan.check(self)

    def test_config_example_shows_each_switch_at_its_default(self):
        defaults = yaml.safe_load((ROOT / "roles/deploy_defaults/defaults/main.yml").read_text())
        shown = example_settings()
        differ = {n: (shown[n], v) for n, v in defaults.items() if n in shown and shown[n] != v}
        self.assertEqual(differ, {}, "config.example is the sample: show the default, or comment it out")


def ansible_sources():
    for path in tracked("roles/*.y*ml", "roles/**/*.y*ml", "roles/**/*.j2", "playbooks/**"):
        name = path.relative_to(ROOT).as_posix()
        if not (name.startswith("roles/galaxy/") or "/molecule/" in name) and path.suffix in (
            ".yml",
            ".yaml",
            ".j2",
        ):
            yield path


def example_settings():
    """{name: value} that config.example sets (not only shows commented)."""
    found = {}
    for path in sorted((ROOT / "config.example" / "group_vars").glob("*.yml")):
        for name, value in (yaml.safe_load(path.read_text()) or {}).items():
            found.setdefault(name, value)
    return found


def role_closure(roles):
    """The roles a play's roles list loads, with their meta dependencies."""
    seen, todo = set(), [r if isinstance(r, str) else r.get("role") or r.get("name") for r in roles]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        meta = ROOT / "roles" / name / "meta" / "main.yml"
        if meta.is_file():
            deps = (yaml.safe_load(meta.read_text()) or {}).get("dependencies") or []
            todo += [d if isinstance(d, str) else d.get("role") or d.get("name") for d in deps]
    return seen


if __name__ == "__main__":
    unittest.main()
