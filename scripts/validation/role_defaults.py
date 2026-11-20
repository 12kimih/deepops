"""A role default a validator shares with the deploy, read from the role itself.

The container images the GPU checks run are pinned once, in roles/deploy_defaults
(tests/container-images keeps them digest-pinned and upstream.py keeps them current);
a validator's default is that value, never a copy. Standard library only: a validator
runs on hosts without PyYAML, so this reads one top-level `name: value` scalar line.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def role_default(role, name):
    """The scalar value of NAME in roles/ROLE/defaults/main.yml."""
    text = (ROOT / "roles" / role / "defaults" / "main.yml").read_text()
    m = re.search(rf"""^{re.escape(name)}:[ \t]*(["']?)([^"'#\s]+)\1[ \t]*(?:#.*)?$""", text, re.M)
    if not m:
        raise KeyError(f"roles/{role}/defaults/main.yml has no scalar {name}")
    return m.group(2)
