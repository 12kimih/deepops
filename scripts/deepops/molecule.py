#!/usr/bin/env python3
"""The roles' molecule scenarios: which there are, and one way to run them.

Usage:
  molecule.py list           the roles with a scenario, as JSON (CI's matrix)
  molecule.py test [ROLE..]  `molecule test --all` in each role (default: every one)

Every scenario lives in roles/<role>/molecule/<scenario>/ and starts from
.config/molecule/config.yml (the platform, driver and Ansible paths). Running through
here names each role's container after the role (DEEPOPS_MOLECULE_INSTANCE), so two
roles tested at once on one Docker host never share, or destroy, a container. CI and
local runs take the same path: the list is the repository's, never a hand copy.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from repo_files import ROOT, tracked  # noqa: E402


def roles():
    """The roles with at least one tracked scenario, sorted."""
    return sorted({p.relative_to(ROOT).parts[1] for p in tracked("roles/*/molecule/*/molecule.yml")})


def instance(role):
    """The container (and inventory host) name prefix of ROLE: a valid hostname."""
    return "molecule-" + role.replace("_", "-").lower()


def test(names):
    known = roles()
    unknown = sorted(set(names) - set(known))
    if unknown:
        print(
            f"no molecule scenario: {', '.join(unknown)} (roles with one: {', '.join(known)})",
            file=sys.stderr,
        )
        return 2
    # molecule and ansible-* from the same environment as this interpreter.
    path = f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"
    failed = []
    for role in names or known:
        env = {**os.environ, "PATH": path, "DEEPOPS_MOLECULE_INSTANCE": instance(role)}
        print(f"==> {role}", flush=True)
        if subprocess.run(["molecule", "test", "--all"], cwd=ROOT / "roles" / role, env=env).returncode:
            failed.append(role)
    if failed:
        print(f"failed: {' '.join(failed)}", file=sys.stderr)
    return 1 if failed else 0


def main(argv):
    if argv[:1] == ["list"] and len(argv) == 1:
        print(json.dumps(roles()))
        return 0
    if argv[:1] == ["test"]:
        return test(argv[1:])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
