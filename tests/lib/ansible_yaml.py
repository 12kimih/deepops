"""Read Ansible YAML the same way whichever style a file is written in.

The fork writes fully qualified module keys (ansible.builtin.command) and
capitalized task names; upstream writes short keys and lower-case names. Tests
check behaviour, so they load files through here and compare names with
`same_name`, which keeps them valid for both spellings.
"""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def canonical(value):
    """Shorten fully qualified module keys: ansible.builtin.command -> command."""
    if isinstance(value, dict):
        return {
            (key.rsplit(".", 1)[-1] if isinstance(key, str) and key.count(".") == 2 else key): canonical(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def load(path):
    """Parse a repo file (path relative to the repo root) and canonicalize it."""
    return canonical(yaml.safe_load((ROOT / path).read_text(encoding="utf-8")))


def same_name(actual, expected):
    """Task names compare without regard to case."""
    return isinstance(actual, str) and isinstance(expected, str) and actual.casefold() == expected.casefold()
