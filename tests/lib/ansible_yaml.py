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


def role_defaults(role):
    """A role's defaults as a play sees them: its meta dependencies' first (recursively,
    in order), then its own over them, as Ansible loads them. A shared value lives in
    a defaults-only role (slurm_paths, prometheus_paths), so a test that read only the
    role's own file would not see it."""
    found = {}
    meta = ROOT / "roles" / role / "meta" / "main.yml"
    for dep in ((yaml.safe_load(meta.read_text()) or {}).get("dependencies") or []) if meta.is_file() else []:
        found.update(role_defaults(dep["role"] if isinstance(dep, dict) else dep))
    own = ROOT / "roles" / role / "defaults" / "main.yml"
    if own.is_file():
        found.update(yaml.safe_load(own.read_text(encoding="utf-8")) or {})
    return found


def role_variables(role):
    """What a role's templates see of it: role_defaults, then its vars/main.yml (role vars
    win over defaults, as in a play)."""
    found = role_defaults(role)
    own = ROOT / "roles" / role / "vars" / "main.yml"
    if own.is_file():
        found.update(yaml.safe_load(own.read_text(encoding="utf-8")) or {})
    return found


def same_name(actual, expected):
    """Task names compare without regard to case."""
    return isinstance(actual, str) and isinstance(expected, str) and actual.casefold() == expected.casefold()


def _templar(variables):
    """A templar over `variables`, trusted as Ansible trusts role defaults and vars files,
    with the repository's filters (plugins/filter), which ansible.cfg gives a real run."""
    from ansible.parsing.dataloader import DataLoader
    from ansible.plugins.loader import filter_loader
    from ansible.template import Templar

    filter_loader.add_directory(str(ROOT / "plugins" / "filter"))
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
