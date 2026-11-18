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


def _templar(variables):
    """A templar over `variables`, trusted as Ansible trusts role defaults and vars files."""
    from ansible.parsing.dataloader import DataLoader
    from ansible.template import Templar

    return Templar(loader=DataLoader(), variables=_trusted(variables))


def _trusted(value):
    """Mark strings as trusted templates (ansible-core 2.19+ renders nothing else)."""
    try:
        from ansible.template import trust_as_template
    except ImportError:  # ansible-core before 2.19 trusts every string
        return value
    if isinstance(value, str):
        return trust_as_template(value)
    if isinstance(value, dict):
        return {k: _trusted(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_trusted(v) for v in value]
    return value


def render(template, variables):
    """Render a template string or structure the way a task would."""
    return _templar(variables).template(_trusted(template))


def condition(when, variables):
    """Evaluate a task's `when` (a string or a list of strings, all must hold)."""
    whens = [when] if isinstance(when, str) else list(when or [])
    templar = _templar(variables)
    if hasattr(templar, "evaluate_conditional"):
        return all(templar.evaluate_conditional(_trusted(w)) for w in whens)
    from ansible.playbook.conditional import Conditional  # ansible-core before 2.19

    check = Conditional(loader=templar._loader)
    check.when = whens
    return check.evaluate_conditional(templar, variables)
