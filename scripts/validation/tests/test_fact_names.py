"""The normalizer's fact names match where Ansible really stores each fact.

normalize-ansible-style.py rewrites ansible_<name> to ansible_facts.<name>,
except for the facts ansible_facts keeps prefixed (custom local facts:
ansible_facts.ansible_local). Deriving that from an assumption once turned
ansible_local into the nonexistent ansible_facts.local and broke every role
that reads local facts, so this asks Ansible itself.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
spec = spec_from_file_location("normalizer", ROOT / "scripts" / "deepops" / "normalize-ansible-style.py")
normalizer = module_from_spec(spec)
spec.loader.exec_module(normalizer)


def gathered_keys():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "keys.json"
        play = Path(tmp) / "play.yml"
        play.write_text(
            "- hosts: localhost\n  connection: local\n  gather_facts: true\n  tasks:\n"
            f"    - ansible.builtin.copy: {{content: '{{{{ ansible_facts.keys() | list | to_json }}}}', dest: '{out}'}}\n"
        )
        (Path(tmp) / "ansible.cfg").write_text("[defaults]\n")
        env = dict(os.environ, ANSIBLE_CONFIG=str(Path(tmp) / "ansible.cfg"))
        playbook = Path(sys.executable).with_name("ansible-playbook")
        subprocess.run(
            [str(playbook), "-i", "localhost,", str(play)],
            env=env,
            check=True,
            capture_output=True,
            timeout=300,
        )
        return set(json.loads(out.read_text()))


class FactNameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = gathered_keys()

    def test_prefixed_facts_are_exactly_the_ones_ansible_prefixes(self):
        prefixed = {k.removeprefix("ansible_") for k in self.keys if k.startswith("ansible_")}
        self.assertEqual(prefixed, set(normalizer.PREFIXED_FACTS))

    def test_listed_names_are_never_stored_prefixed(self):
        for name in normalizer.FACT_NAMES:
            with self.subTest(fact=name):
                self.assertFalse(f"ansible_{name}" in self.keys and name not in self.keys)

    def test_rewrites(self):
        cases = {
            "when: ansible_distribution == 'Ubuntu'": "when: ansible_facts.distribution == 'Ubuntu'",
            'x: "{{ ansible_local.gpus.count }}"': 'x: "{{ ansible_facts.ansible_local.gpus.count }}"',
            'x: "{{ ansible_facts.local.gpus }}"': 'x: "{{ ansible_facts.ansible_local.gpus }}"',
            "x: \"{{ hostvars[h]['ansible_local'] }}\"": "x: \"{{ hostvars[h]['ansible_facts']['ansible_local'] }}\"",
            "x: \"{{ hostvars[h]['ansible_default_ipv4'] }}\"": "x: \"{{ hostvars[h]['ansible_facts']['default_ipv4'] }}\"",
            "  ansible.builtin.setup: filter=ansible_local": "  ansible.builtin.setup: filter=ansible_local",
            '{% set m = hostvars[n]["ansible_facts"]["local"] %}': '{% set m = hostvars[n]["ansible_facts"]["ansible_local"] %}',
        }
        for before, after in cases.items():
            with self.subTest(line=before):
                self.assertEqual(normalizer.fix_facts(before), after)
                # A second pass changes nothing.
                self.assertEqual(normalizer.fix_facts(after), after)


if __name__ == "__main__":
    unittest.main()
