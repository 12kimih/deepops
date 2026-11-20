#!/usr/bin/env python3
"""Print the decrypted value of a vaulted variable from config/.

ansible-core 2.19+ redacts vaulted values in debug output ($REDACTED$), so
`ansible -m debug -a var=X` no longer shows a secret. This finds X as an
inline `!vault` value in config/group_vars and config/host_vars and decrypts it
with ansible-vault, which reads the vault password file from ansible.cfg.

Usage (from the deepops directory):
  uv run scripts/deepops/vault_show.py bmc_password            # where it is defined once
  uv run scripts/deepops/vault_show.py bmc_password --host gpu3  # a host override wins
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(os.environ.get("DEEPOPS_CONFIG_DIR", ROOT / "config"))


class Loader(yaml.SafeLoader):
    pass


class Vaulted(str):
    pass


Loader.add_constructor("!vault", lambda loader, node: Vaulted(loader.construct_scalar(node)))


SUFFIXES = ("", ".yml", ".yaml", ".json")


def var_files(kind, name=None):
    """The files Ansible reads for KIND (group_vars, host_vars): NAME, NAME.yml,
    NAME.yaml, NAME.json, or every file in the directory NAME/; every name when None."""
    base = CONFIG / kind
    if not base.is_dir():
        return []
    entries = [base / name] if name else sorted(base.iterdir())
    files = []
    for entry in entries:
        if entry.is_dir():
            files += sorted(p for p in entry.rglob("*") if p.is_file() and p.suffix in SUFFIXES)
        for suffix in SUFFIXES if name else ("",):
            path = entry.with_name(entry.name + suffix)
            if path.is_file() and path.suffix in SUFFIXES:
                files.append(path)
    return files


def vaulted_in(files, name):
    found = []
    for path in files:
        data = yaml.load(path.read_text(), Loader=Loader) or {}
        if isinstance(data, dict) and isinstance(data.get(name), Vaulted):
            found.append((path, data[name]))
    return found


def several(name, found, hint):
    return f"{name} is vaulted in several files ({', '.join(str(p.relative_to(CONFIG)) for p, _ in found)}); {hint}"


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name")
    parser.add_argument("--host", help="look in this host's host_vars first")
    args = parser.parse_args(argv)
    # A host's own value wins; among group values, which one a host sees depends on its
    # groups, so more than one is never chosen silently.
    host = vaulted_in(var_files("host_vars", args.host), args.name) if args.host else []
    if len(host) > 1:
        sys.exit(several(args.name, host, "keep one"))
    found = host or vaulted_in(var_files("group_vars"), args.name)
    if not found:
        sys.exit(
            f"{args.name}: no !vault value in {CONFIG}/group_vars{' or host_vars/' + args.host if args.host else ''}"
        )
    if len(found) > 1:
        sys.exit(several(args.name, found, "set it once, or for the host in host_vars and pass --host"))
    vault = shutil.which("ansible-vault", path=str(Path(sys.executable).parent)) or "ansible-vault"
    # The secret as bytes, exactly: decoding could change one that is not UTF-8.
    result = subprocess.run(
        [vault, "decrypt", "--output", "-"], input=found[0][1].encode(), cwd=ROOT, capture_output=True
    )
    if result.returncode != 0:
        sys.exit(result.stderr.decode(errors="replace").strip())
    sys.stdout.buffer.write(result.stdout if result.stdout.endswith(b"\n") else result.stdout + b"\n")


if __name__ == "__main__":
    main(sys.argv[1:])
