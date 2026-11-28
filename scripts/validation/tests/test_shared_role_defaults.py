"""A variable several roles need is defined once.

A copy per role must be kept equal by hand, and a role whose fallback drifted
quietly disagrees with the others wherever config/ is silent; a fallback written as
`| default(<value>)` (or its alias `| d(...)`) is such a copy too, a true/false one
included, and in a play that does not load the owning role it always wins. So a
shared value lives in one defaults-only role (prometheus_paths, slurm_paths,
deepops_paths) that the roles reading it depend on, and this test fails on a second
definition, in defaults or vars, or a literal fallback of a defined variable.

A role default exists only where its role is loaded. So a play reading one lists the
owning role in its roles (deploy_defaults for what a deploy includes, kubespray_defaults
for Kubespray's settings; an import's `when` is evaluated in the imported plays, which
list it too), and a role reading another's names it in its meta dependencies. Without
that, config/ restating the value was all that kept it defined. A setting
config.example shows but no role defines has no default at all, so a fallback there is
a second, unchecked one.
"""

import os
import re
import subprocess
import sys
import unittest
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import Scan, defined_here, load, references, role_var_files, tracked  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import settings  # noqa: E402

KUBESPRAY = "kubespray"
ROLES_PATH = [ROOT / "roles", ROOT / "roles" / "galaxy", ROOT / "submodules" / "kubespray" / "roles"]

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
# A variable read with a fallback or a defined test (not an attribute: x.y is defined).
GUARDED = re.compile(
    r"(?<![\w.])([a-z_][a-z0-9_]*)\s*(?:\|\s*(?:default|d)\(|is\s+(?:not\s+)?(?:defined|undefined)\b)"
)
# Ansible's own names.
IMPLICIT = {"item", "hostvars", "groups", "omit", "inventory_hostname", "group_names", "playbook_dir"}
# Kubespray's variables our plays set for it (ansible_become, its own switches).
KUBESPRAY_READS = set(
    subprocess.run(
        ["git", "grep", "-h", "-o", "-E", r"[a-z_][a-z0-9_]*", "--", "roles", "playbooks"],
        cwd=ROOT / "submodules" / "kubespray", capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    ).stdout.split()
)  # fmt: skip
# What no role can own, and why.
# (hostlist, which imports also pass as vars, and the per-host values read through
# another host's hostvars, which hold no role defaults, count as defined there.)
UNOWNED = {
    # Play parameters given with -e: read in `serial:` or a play's vars, before any role.
    "maintenance_serial",
}  # fmt: skip
# A whole word: a name made only of word characters is used where it is one.
WORD = re.compile(r"\w+")
DEFINED_TEST = re.compile(r"\b([a-z_][a-z0-9_]*)\s+is\s+(?:not\s+)?(?:defined|undefined)\b")
# Roles that read another role's default without loading it, and why.
ROLE_READS_OK = {
    # runai finds a GPU Operator release some other play installed (FALLBACK_OK).
    ("runai", "gpu_operator_release_name"),
}
# Plays that read a role default without its role, where the fallback they write is
# that default.
PLAY_READS_OK: set = set()


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
    maxDiff = None

    def test_a_shared_variable_is_defined_once(self):
        twice = {n: sorted(r) for n, r in role_defaults().items() if len(r) > 1 and n not in STANDALONE}
        self.assertEqual(twice, {}, "define it once, in a shared defaults role the readers depend on")
        self.assertGreater(len(role_defaults()), 500, "the scan found too few role variables")

    def test_no_literal_fallback_restates_a_defined_variable(self):
        owners = role_defaults()
        scan, offenders = Scan(FALLBACK_OK, minimum=200), []
        for path in map(scan.seen, ansible_sources()):
            name = path.relative_to(ROOT).as_posix()
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

    def test_no_defined_test_guards_a_role_default(self):
        """`x is defined` on a variable a role defines is a fallback in another form:
        always true where the role is loaded, and where it is not, the play silently
        takes the other branch (container_manager once skipped a cleanup that way)."""
        owners = settings.owners()
        scan, offenders = Scan(PLAY_READS_OK, minimum=200), []
        for path in map(scan.seen, ansible_sources()):
            name = path.relative_to(ROOT).as_posix()
            for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                for var in DEFINED_TEST.findall(line):
                    if var in owners and not scan.excuse((name, var)):
                        offenders.append(f"{name}:{number}: {var} is defined")
        self.assertEqual(offenders, [], "the variable has a default: read it, loading its role")
        scan.check(self)

    def test_a_guarded_setting_has_an_owner(self):
        """A setting read through `| default(...)` or `is defined` is one a site may leave
        out, so it needs a default (empty for unset) in the role that reads it: then
        "unset" is spelled once, config.example can show it, and settings.py and
        config_lint see it. Only what no role can own is exempt (UNOWNED)."""
        owners, local = settings.owners(), set()
        for path in ansible_sources():
            if path.suffix in (".yml", ".yaml") and "/templates/" not in path.as_posix():
                data = load(path)
                local |= defined_here(data) | (
                    set(data) if "/vars/" in str(path) and isinstance(data, dict) else set()
                )
        scan, offenders = Scan(UNOWNED, minimum=200), []
        for path in map(scan.seen, ansible_sources()):
            name = path.relative_to(ROOT).as_posix()
            for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                for m in GUARDED.finditer(line):
                    var = m.group(1)
                    if line[: m.start()].rstrip().endswith("|"):
                        continue  # a filter's name (x | first | default(...))
                    if var in owners or var in local or var.startswith("ansible_") or var in IMPLICIT:
                        continue
                    if not scan.excuse(var):
                        offenders.append(f"{name}:{number}: {var}")
        self.assertEqual(offenders, [], "give it a default in the role that reads it (or deploy_defaults)")
        scan.check(self)

    def test_a_value_passed_is_read(self):
        """A `vars:` entry of a play, an import, a task or a role that nothing reads is a
        switch that does nothing (nvidia_docker_skip_docker_reload outlived its reader)."""
        # Every whole word of every source, counted once: a name's uses are its count (a
        # regex per name over every file took 90 s).
        sources = [
            p.read_text(errors="ignore") for p in tracked("roles", "playbooks", "plugins") if p.is_file()
        ]
        words = Counter(w for text in sources for w in WORD.findall(text))
        scan, unread = Scan(minimum=150), []
        for path in map(scan.seen, [p for p in ansible_sources() if p.suffix in (".yml", ".yaml")]):
            if (
                "/templates/" in path.as_posix()
                or "/defaults/" in path.as_posix()
                or "/vars/" in path.as_posix()
            ):
                continue
            for name in sorted(passed_vars(load(path))):
                # Its own `name:` key in the vars block is one occurrence; a reader adds more.
                if name.startswith("ansible_") or name in KUBESPRAY_READS:
                    continue  # Ansible's own connection settings, or Kubespray's
                if (words[name] if WORD.fullmatch(name) else uses(name, sources)) <= 1:
                    unread.append(f"{path.relative_to(ROOT)}: {name}")
        self.assertEqual(unread, [], "nothing reads it: drop it, or restore its reader")
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

    def test_a_play_reading_a_role_default_loads_its_owner(self):
        """A role default exists only in a play that loads its role: elsewhere the
        variable is undefined, or a fallback silently wins. So a play reading one lists
        the owning role (deploy_defaults, a shared defaults role, kubespray_defaults),
        and so do the plays an import's `when` reads it in."""
        owners = settings.owners()
        scan, missing = Scan(PLAY_READS_OK, minimum=80), []

        def check(path, play, used):
            loaded = role_closure(list(play.get("roles") or []) + imported_roles(play))
            local = defined_here(play)
            for name in sorted(used - local):
                owner = owners.get(name)
                if owner is None or owner.roles & loaded:
                    continue
                rel = path.relative_to(ROOT).as_posix()
                if not scan.excuse((rel, name)):
                    missing.append(f"{rel}: play {play.get('name') or play.get('hosts')} reads {name} "
                                   f"({', '.join(sorted(owner.roles))})")  # fmt: skip

        def plays(path):
            data = yaml.safe_load(path.read_text())
            return [p for p in data if isinstance(p, dict)] if isinstance(data, list) else []

        def imported(path, used):
            for play in plays(path):
                if "import_playbook" in play:
                    imported((path.parent / play["import_playbook"]).resolve(), used)
                else:
                    check(path, play, used)

        for path in map(scan.seen, tracked("playbooks/*.yml", "playbooks/**/*.yml")):
            for play in plays(path):
                if "import_playbook" in play:
                    target = (path.parent / play["import_playbook"]).resolve()
                    # Kubespray's own playbooks load kubespray_defaults themselves.
                    if KUBESPRAY not in target.parts:
                        imported(target, references({"when": play.get("when")}))
                else:
                    params = [
                        {k: v for k, v in r.items() if k not in ("role", "name")}
                        for r in play.get("roles") or [] if isinstance(r, dict)
                    ]  # fmt: skip
                    body = {k: v for k, v in play.items() if k not in ("hosts", "name", "roles")}
                    check(path, play, references(body) | references(params))
        self.assertEqual(missing, [], "list the owning role in the play's roles")
        scan.check(self)

    def test_a_role_reading_another_roles_default_depends_on_it(self):
        """Inside a role, another role's default exists only when that role is loaded
        too: a meta dependency, or an include. A fallback to exactly the owner's empty
        default is the one alternative (the reader works without the owner)."""
        owners = settings.owners()
        scan, missing = Scan(ROLE_READS_OK, minimum=80), []
        roles = sorted({p.relative_to(ROOT).parts[1] for p in tracked("roles/*/*/*")} - {"galaxy"})
        for role in map(scan.seen, roles):
            used, local, included = role_reads(role)
            loaded = role_closure([role] + included)
            for name in sorted(used - local):
                owner = owners.get(name)
                if owner is None or owner.roles & loaded or scan.excuse((role, name)):
                    continue
                if owner.value in ("", [], {}, None) and empty_fallbacks_only(role, name):
                    continue
                missing.append(f"roles/{role} reads {name} ({', '.join(sorted(owner.roles))})")
        self.assertEqual(missing, [], "add the owning role (or a shared defaults role) to meta dependencies")
        scan.check(self)


def role_reads(role):
    """(names ROLE's tasks, handlers, defaults, vars and templates use, names it defines
    itself, roles it includes or imports)."""
    used, local, included = set(), set(), []
    for path in tracked(f"roles/{role}/**"):
        rel = path.relative_to(ROOT).as_posix()
        if "/molecule/" in rel or "/files/" in rel:
            continue
        text = path.read_text(errors="ignore")
        if "/templates/" in rel:
            used |= references(text)
        elif path.suffix in (".yml", ".yaml") and rel.split("/")[2] in (
            "tasks",
            "handlers",
            "defaults",
            "vars",
        ):
            data = yaml.safe_load(text)
            used |= references(data)
            local |= defined_here(data) | (set(data) if isinstance(data, dict) else set())
            included += [r for r in named_roles(data, INCLUDES)]
    return used, local, included


INCLUDES = ("include_role", "ansible.builtin.include_role", "import_role", "ansible.builtin.import_role")


def named_roles(node, keys):
    """The `name` of every KEYS task in NODE."""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in keys and isinstance(v, dict) and v.get("name"):
                found.append(v["name"])
            found += named_roles(v, keys)
    elif isinstance(node, list):
        for item in node:
            found += named_roles(item, keys)
    return found


def uses(name, sources):
    """How often NAME stands alone in SOURCES (for a name that is not one word)."""
    use = re.compile(rf"(?<![\w]){re.escape(name)}(?![\w])")
    return sum(len(use.findall(text)) for text in sources)


def passed_vars(node):
    """Names a `vars:` block anywhere in NODE passes."""
    found = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "vars" and isinstance(v, dict):
                found |= {n for n in v if isinstance(n, str)}
            found |= passed_vars(v)
    elif isinstance(node, list):
        for item in node:
            found |= passed_vars(item)
    return found


def empty_fallbacks_only(role, name):
    """Whether every use of NAME in ROLE falls back to an empty literal."""
    use = re.compile(rf"(?<![\w.]){re.escape(name)}(?!\w)(\s*\|\s*(?:default|d)\(\s*([^),]*))?")
    for path in tracked(f"roles/{role}/**"):
        if "/molecule/" in path.as_posix():
            continue
        for m in use.finditer(path.read_text(errors="ignore")):
            if m.group(2) is None or m.group(2).strip() not in EMPTY:
                return False
    return True


def ansible_sources():
    """Our YAML and templates; a role's templates whatever their suffix (a .sh, a .conf)."""
    for path in tracked("roles/*.y*ml", "roles/**/*.y*ml", "roles/**/templates/**", "playbooks/**"):
        name = path.relative_to(ROOT).as_posix()
        if name.startswith("roles/galaxy/") or "/molecule/" in name:
            continue
        if path.suffix in (".yml", ".yaml", ".j2") or "/templates/" in name:
            yield path


def example_settings():
    """{name: value} that config.example sets or shows commented."""
    found = {}
    for path in tracked("config.example/group_vars/*.yml"):
        for entry in settings.entries(path.read_text()):
            found.setdefault(entry.name, entry.value)
    return found


def role_closure(roles):
    """The roles a play's roles list loads, with their meta dependencies, found on
    ansible.cfg's roles_path (ours, Galaxy's, Kubespray's)."""
    seen, todo = set(), [r if isinstance(r, str) else r.get("role") or r.get("name") for r in roles]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        for base in ROLES_PATH:
            meta = base / name / "meta" / "main.yml"
            if meta.is_file():
                deps = (yaml.safe_load(meta.read_text()) or {}).get("dependencies") or []
                todo += [d if isinstance(d, str) else d.get("role") or d.get("name") for d in deps]
                break
    return seen


def imported_roles(play):
    """Roles a play imports statically (import_role), whose defaults it sees."""
    return named_roles(play, ("import_role", "ansible.builtin.import_role"))


if __name__ == "__main__":
    unittest.main()
