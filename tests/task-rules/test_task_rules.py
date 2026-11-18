"""Repository-wide rules for tasks that keep `--check --diff` previews correct and safe.

1. A command/shell/script task that only reads (changed_when: false) and registers a
   result must set check_mode: false. Under --check Ansible skips commands, so the
   registered result is empty and every task that reads it fails or decides wrongly.
2. A template task whose template renders a secret must set diff: false, or a
   --diff preview prints the secret.
3. No expression reads the internal `vars` dictionary (`x in vars`, `vars[...]`):
   ansible-core 2.20 deprecates it and 2.24 removes it. Test `x is defined`, or use
   the vars/varnames lookups for a computed name.
"""

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = {
    "command",
    "shell",
    "script",
    "ansible.builtin.command",
    "ansible.builtin.shell",
    "ansible.builtin.script",
}
TEMPLATES = {"template", "ansible.builtin.template"}
SECRET = re.compile(
    r"\{\{[^}]*(password|passwd|_token|secret|webhook|api_key|_pass\b|heartbeat_url)[^}]*\}\}"
)


def task_files():
    for path in sorted(ROOT.glob("roles/*/tasks/**/*.yml")) + sorted(ROOT.glob("playbooks/**/*.yml")):
        if "galaxy" not in path.parts:
            yield path


def tasks(path):
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, list):
        return
    plays = (
        data
        if data and isinstance(data[0], dict) and ("hosts" in data[0] or "import_playbook" in data[0])
        else None
    )
    lists = [
        p.get(k) for p in plays or [] if isinstance(p, dict) for k in ("pre_tasks", "tasks", "post_tasks")
    ]
    stack = lists if plays is not None else [data]
    while stack:
        for task in stack.pop() or []:
            if isinstance(task, dict):
                stack += [task.get(k) for k in ("block", "rescue", "always")]
                yield task


def template_path(path, src):
    """The role template a task renders; a `{{ var }}` src is resolved from the role defaults."""
    if "roles" not in path.parts:
        return None
    role = ROOT / "roles" / path.parts[path.parts.index("roles") + 1]
    var = re.fullmatch(r"\{\{\s*(\w+)\s*\}\}", src)
    if var:
        defaults = role / "defaults" / "main.yml"
        src = str(
            ((yaml.safe_load(defaults.read_text()) if defaults.is_file() else None) or {}).get(
                var.group(1), ""
            )
        )
    if not src or "{{" in src:
        return None
    candidate = role / "templates" / src
    return candidate if candidate.is_file() else None


class TaskRules(unittest.TestCase):
    def test_read_only_commands_run_under_check(self):
        offenders = [
            f"{path.relative_to(ROOT)}: {task.get('name')}"
            for path in task_files()
            for task in tasks(path)
            if COMMANDS & set(task)
            and str(task.get("changed_when", "")).lower() == "false"
            and "register" in task
            and "check_mode" not in task
        ]
        self.assertEqual(offenders, [], "add check_mode: false to these read-only tasks")

    def test_secret_templates_hide_their_diff(self):
        offenders = []
        for path in task_files():
            for task in tasks(path):
                module = next((task[k] for k in TEMPLATES if k in task), None)
                if not isinstance(module, dict) or task.get("diff") is False:
                    continue
                src = template_path(path, str(module.get("src", "")))
                if src and SECRET.search(src.read_text()):
                    offenders.append(f"{path.relative_to(ROOT)}: {task.get('name')}")
        self.assertEqual(offenders, [], "add diff: false to these template tasks")

    def test_no_internal_vars_dictionary(self):
        pattern = re.compile(r"(?<![\w.])(?:in\s+vars\b|vars\[)")
        offenders = [
            f"{path.relative_to(ROOT)}:{number}"
            for path in list(task_files()) + sorted(ROOT.glob("roles/*/templates/**/*.j2"))
            for number, line in enumerate(path.read_text().splitlines(), 1)
            if not line.lstrip().startswith("#") and pattern.search(line)
        ]
        self.assertEqual(offenders, [], "the internal vars dictionary is deprecated")


if __name__ == "__main__":
    unittest.main()
