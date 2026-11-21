"""An in-tree Ansible module loaded as Ansible ships it, for the tests: with every file of
plugins/module_utils importable as ansible.module_utils.<name> (ansible.cfg module_utils).

    mod = in_tree.load("roles/stale_files/library/stale_files.py")   # its functions
    python3 tests/lib/in_tree.py <module.py> <args.json>             # run it as Ansible does

The one place tests register module_utils, so a new shared file needs no test edits.
"""

import importlib.util
import runpy
import sys

from repo_scan import ROOT, tracked


def register_module_utils():
    for path in tracked("plugins/module_utils/*.py"):
        name = f"ansible.module_utils.{path.stem}"
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(name, path)
            sys.modules[name] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules[name])


def load(rel, name=None):
    """The module at REL (from the repository root), imported under NAME (its file name)."""
    register_module_utils()
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name or path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    register_module_utils()
    module, sys.argv = sys.argv[1], sys.argv[1:]
    runpy.run_path(module, run_name="__main__")
