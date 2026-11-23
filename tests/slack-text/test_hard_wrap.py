"""No text bound for Slack is hard-wrapped. Slack breaks a line wherever the text has
a newline, so a paragraph wrapped at the editor's width (a YAML literal block, a
string continued over lines) shows as broken mid-sentence (owner, 2026-11-18; the
user docs fixed their own case by unwrapping before posting). A line break in such a
text is deliberate (a list, a placeholder that expands to lines, a new sentence), so a
newline between two lower-case words is refused, here, at the source:

  - every string of the modules that build Slack messages (the cluster bot, and the
    cluster_tools modules that import slack, notices or disk_report), docstrings aside;
  - every role's *_text setting (a DM, a notice), whose prose belongs in a folded >-
    block or one line;
  - every alert summary and description, which Alertmanager posts.
Terminal help texts are wrapped for a terminal and are not Slack's."""

import ast
import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

# A newline inside a sentence: a word, comma or semicolon before it, a word after it.
WRAP = re.compile(r"[a-z,;]\n[a-z(]")
TOOLS = "roles/cluster_tools_lib/files/cluster_tools"
SLACK_TEXT_MODULES = ("slack", "notices", "disk_report")
IMPORTS_SLACK_TEXT = re.compile(
    rf"from \. import [^\n]*\b({'|'.join(SLACK_TEXT_MODULES)})\b|from \.({'|'.join(SLACK_TEXT_MODULES)}) import"
)


def slack_modules():
    found = [Path(p) for p in tracked("roles/cluster_bot/files/cluster_bot/*.py")]
    for path in map(Path, tracked(f"{TOOLS}/*.py")):
        if path.stem in SLACK_TEXT_MODULES or IMPORTS_SLACK_TEXT.search((ROOT / path).read_text()):
            found.append(path)
    return sorted(set(found))


def wrapped_strings(source, name):
    """name:line of each string in SOURCE, docstrings aside, with a newline inside a
    sentence."""
    tree = ast.parse(source, name)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)
    }  # fmt: skip
    return [
        f"{name}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
        and WRAP.search(node.value)
    ]  # fmt: skip


class HardWrap(unittest.TestCase):
    def test_the_slack_modules_are_found(self):
        names = {p.stem for p in slack_modules()}
        self.assertTrue({"slack", "usage_report", "job_notice", "app", "home_blocks"} <= names, names)

    def test_no_slack_string_is_wrapped(self):
        offenders = [hit for p in slack_modules() for hit in wrapped_strings((ROOT / p).read_text(), str(p))]
        self.assertEqual(offenders, [], "join the lines: Slack breaks a line at every newline")

    def test_no_text_setting_is_wrapped(self):
        offenders = []
        for path in map(Path, tracked("roles/*/defaults/main.yml")):
            for key, value in (yaml.safe_load((ROOT / path).read_text()) or {}).items():
                if re.search(r"_text(_\w+)?$", key) and isinstance(value, str) and WRAP.search(value):
                    offenders.append(f"{path}: {key}")
        self.assertEqual(offenders, [], "write the prose as a folded >- block or one line")

    def test_no_alert_text_is_wrapped(self):
        offenders = []
        for path in map(Path, tracked("roles/prometheus/files/rules/*.yml")):
            for group in yaml.safe_load((ROOT / path).read_text())["groups"]:
                for rule in group["rules"]:
                    for key, value in (rule.get("annotations") or {}).items():
                        if WRAP.search(value):
                            offenders.append(f"{path}: {rule.get('alert')} {key}")
        self.assertEqual(offenders, [], "use a folded >- block: Alertmanager posts it to Slack as written")

    def test_the_rule_tells_a_wrap_from_a_deliberate_break(self):
        self.assertTrue(WRAP.search("your jobs have had idle GPUs for long, and\nwill be cancelled"))
        for fine in (
            "Idle jobs:\n{jobs}\nPlease free them.",
            "- one\n- two",
            "Done.\nNext line",
            "```\ncode\n```",
        ):
            with self.subTest(text=fine):
                self.assertIsNone(WRAP.search(fine))
        self.assertEqual(wrapped_strings('x = "a wrapped\\nsentence"\n', "m.py"), ["m.py:1"])
        self.assertEqual(wrapped_strings('def f():\n    """a wrapped\n    docstring"""\n', "m.py"), [])


if __name__ == "__main__":
    unittest.main()
