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


def candidates(host):
    files = sorted((CONFIG / "group_vars").glob("*.y*ml"))
    if host:
        files = [
            p for p in (CONFIG / "host_vars" / host, CONFIG / "host_vars" / f"{host}.yml") if p.is_file()
        ] + files
    return files


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name")
    parser.add_argument("--host", help="look in this host's host_vars first")
    args = parser.parse_args(argv)
    found = []
    for path in candidates(args.host):
        data = yaml.load(path.read_text(), Loader=Loader) or {}
        if isinstance(data, dict) and isinstance(data.get(args.name), Vaulted):
            found.append((path, data[args.name]))
    if not found:
        sys.exit(
            f"{args.name}: no !vault value in {CONFIG}/group_vars{' or host_vars/' + args.host if args.host else ''}"
        )
    if len(found) > 1 and not args.host:
        sys.exit(
            f"{args.name} is vaulted in several files ({', '.join(str(p.relative_to(CONFIG)) for p, _ in found)}); "
            "pass --host to choose"
        )
    vault = shutil.which("ansible-vault", path=str(Path(sys.executable).parent)) or "ansible-vault"
    result = subprocess.run(
        [vault, "decrypt", "--output", "-"], input=found[0][1], cwd=ROOT, capture_output=True, text=True
    )
    if result.returncode != 0:
        sys.exit(result.stderr.strip())
    print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
