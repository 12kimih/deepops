"""A variable several roles need is defined once.

A copy per role must be kept equal by hand, and a role whose fallback drifted
quietly disagrees with the others wherever config/ is silent; a fallback written as
`| default(<literal>)` is such a copy too, and in a play that does not load the
owning role it always wins. So a shared value lives in one defaults-only role
(prometheus_paths, slurm_paths, deepops_paths) that the roles reading it depend on,
and this test fails on a second definition or a literal fallback of a defined one.
"""

from collections import defaultdict
from pathlib import Path
import re
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[3]

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
FALLBACK = re.compile(r"([a-z_][a-z0-9_]*)\s*\|\s*default\(\s*([^)]*)\)")
EMPTY = {"omit", "none", "None", "", "''", '""', "[]", "{}", "false", "False", "true", "True"}


def role_defaults():
    found = defaultdict(dict)
    for path in sorted(ROOT.glob("roles/*/defaults/main.yml")) + sorted(ROOT.glob("roles/*/vars/main.yml")):
        role = path.parts[-3]
        if role == "galaxy":
            continue
        data = yaml.safe_load(path.read_text()) or {}
        for name, value in data.items():
            # Leading underscore marks a role-private helper, free to differ.
            if not name.startswith("_") and (name, role) not in INTENTIONAL:
                found[name][role] = value
    return found


class SharedRoleDefaultTests(unittest.TestCase):
    def test_a_shared_variable_is_defined_once(self):
        twice = {n: sorted(r) for n, r in role_defaults().items() if len(r) > 1 and n not in STANDALONE}
        self.assertEqual(twice, {}, "define it once, in a shared defaults role the readers depend on")

    def test_no_literal_fallback_restates_a_defined_variable(self):
        owners = role_defaults()
        out = subprocess.run(
            ["git", "ls-files", "-z", "--", "roles", "playbooks"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        offenders = []
        for name in filter(None, out.split("\0")):
            if name.startswith("roles/galaxy/") or not name.endswith((".yml", ".yaml", ".j2")):
                continue
            role = name.split("/")[1] if name.startswith("roles/") else None
            for number, line in enumerate((ROOT / name).read_text(errors="ignore").splitlines(), 1):
                for var, arg in FALLBACK.findall(line):
                    if (
                        var in owners
                        and arg.split(",")[0].strip() not in EMPTY
                        and role not in owners[var]
                        and (name, var) not in FALLBACK_OK
                    ):
                        offenders.append(f"{name}:{number}: {var} | default({arg})")
        self.assertEqual(offenders, [], "depend on the role that defines it instead of restating its value")


if __name__ == "__main__":
    unittest.main()
