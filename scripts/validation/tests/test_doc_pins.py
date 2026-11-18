"""Keep version pins quoted in the docs equal to the role defaults.

A doc that restates `name: version` for a role variable goes stale the next
time the role is bumped. Docs should show a placeholder and point at the role;
where they do quote a value, it must be the current default (or the value the
example config sets).
"""

from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[3]
PIN = re.compile(r"\b([a-z][a-z0-9_]*(?:version|_container|_image|_tag)[a-z0-9_]*)\s*[:=]\s*[\"']?([vV]?\d[\w.\-:/]*)")


def role_defaults():
    found = {}
    for path in ROOT.glob("roles/*/defaults/main.yml"):
        if "galaxy" in path.parts:
            continue
        for name, value in (yaml.safe_load(path.read_text()) or {}).items():
            if isinstance(value, (str, int, float)) and "{{" not in str(value):
                found.setdefault(name, str(value))
    return found


def example_values():
    found = {}
    for path in ROOT.glob("config.example/group_vars/*.yml"):
        for line in path.read_text().splitlines():
            m = re.match(r"^([a-z][a-z0-9_]+):\s*[\"']?([^\"'#\s]+)", line)
            if m:
                found[m.group(1)] = m.group(2)
    return found


class DocPinTests(unittest.TestCase):
    def test_quoted_pins_match_defaults(self):
        defaults, examples = role_defaults(), example_values()
        docs = list(ROOT.glob("docs/**/*.md")) + list(ROOT.glob("roles/*/README.md")) + [ROOT / "README.md"]
        for doc in docs:
            for number, line in enumerate(doc.read_text().splitlines(), 1):
                for name, value in PIN.findall(line):
                    value = value.rstrip(".,)`\"'")
                    if name in defaults and value not in (defaults[name], examples.get(name)):
                        with self.subTest(doc=str(doc.relative_to(ROOT)), line=number):
                            self.fail(f"{name}={value} but the role default is {defaults[name]}")


if __name__ == "__main__":
    unittest.main()
