"""Keep variables that several roles default identical across those roles.

Roles carry their own defaults so each one runs on its own, which means a
variable shared by several roles is written down once per role. These copies
must agree: a site sets the value once in config/, and a role whose fallback
drifted would quietly disagree with the others wherever config/ is silent.
"""

from collections import defaultdict
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[3]

# Deliberate per-role overrides: the DGX role points cachefilesd at its RAID.
INTENTIONAL = {
    ("cachefilesd_cache_dir", "nvidia-dgx"),
    ("cachefilesd_cache_tag", "nvidia-dgx"),
}


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
    def test_shared_defaults_agree(self):
        for name, by_role in role_defaults().items():
            if len(by_role) < 2:
                continue
            with self.subTest(variable=name):
                values = {repr(v) for v in by_role.values()}
                self.assertEqual(len(values), 1, f"{name} differs across roles: {by_role}")


if __name__ == "__main__":
    unittest.main()
