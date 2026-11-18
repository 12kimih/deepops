"""roles/ai_agent_rules/templates/requirements.toml.j2 must turn any rules text into a
Codex requirements.toml that parses back to exactly that text."""

import tomllib
import unittest
from pathlib import Path

from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

TEMPLATE = Path(__file__).resolve().parents[2] / "roles/ai_agent_rules/templates/requirements.toml.j2"
TRICKY = (
    "<!-- cluster-agent-rules -->\n# 규칙\n\n"
    "- \"double\" and 'single' quotes, ''' and \"\"\" runs\n"
    "- backslashes \\ \\n \\u0041, tabs\there, ${not_a_var} and {{ braces }}\n"
    "- emoji 😀, NUL-free control \x07 bell\n"
)


class RequirementsTemplateTest(unittest.TestCase):
    def render(self, text):
        templar = Templar(
            loader=DataLoader(), variables={"ai_agent_rules_text": text, "ansible_managed": "Ansible managed"}
        )
        return templar.template(trust_as_template(TEMPLATE.read_text()))

    def test_round_trip(self):
        for text in (TRICKY, "<!-- cluster-agent-rules -->\n", "x" * 24000):
            parsed = tomllib.loads(self.render(text))
            self.assertEqual(parsed, {"additional_developer_instructions": text})


if __name__ == "__main__":
    unittest.main()
