"""A variable several roles need is defined once.

A copy per role must be kept equal by hand, and a role whose fallback drifted
quietly disagrees with the others wherever config/ is silent; a fallback written as
`| default(<value>)` (or its alias `| d(...)`) is such a copy too, a true/false one
included, and in a play that does not load the owning role it always wins. So a
shared value lives in one defaults-only role (prometheus_paths, slurm_paths,
deepops_paths) that the roles reading it depend on, and this test fails on a second
definition, in defaults or vars, or a literal fallback of a defined variable.
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
# A play's own conditions are evaluated before any role is loaded, so they cannot see
# role defaults and need a fallback; there it must equal the owner's default.
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
                        if role is None and all(yaml.safe_load(literal) == v for v in owners[var].values()):
                            continue  # a play's condition, restating the owner's value exactly
                        if not scan.excuse((name, var)):
                            offenders.append(f"{name}:{number}: {var} | default({arg})")
        self.assertEqual(offenders, [], "depend on the role that defines it instead of restating its value")
        scan.check(self)


if __name__ == "__main__":
    unittest.main()
