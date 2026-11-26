"""The commands usage replaced (cluster_tools_retired_builtin: gpu-usage, disk-usage,
scratch-space) are named nowhere else: a page, a notice, a help line or a test that still
said one sent people to a command that is gone (owner, 2026-11-27: no old names kept).
A word that only looks like one is allowed by file and line, with why."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

import yaml  # noqa: E402

RETIRED = yaml.safe_load((ROOT / "roles/cluster_tools_defaults/defaults/main.yml").read_text())[
    "cluster_tools_retired_builtin"
]
# A command's name as a word: not a path, a file name (scratch-space.yml), a longer
# name (scratch-space-purge) or a channel (#gpu-usage).
WORD = re.compile(r"(?<![#\w/.-])(" + "|".join(map(re.escape, RETIRED)) + r")(?![\w.-])")
# (file, a pattern of the line) -> why it is not the command.
ALLOWED = {
    ("roles/cluster_tools_defaults/defaults/main.yml", r"^cluster_tools_retired_builtin:"): "the list itself",
    ("playbooks/generic/scratch-space.yml", r"^\s+- scratch-space$"): "the playbook's tag, its own name",
    (
        "roles/prometheus/files/rules/scratch-space.yml",
        r"- name: scratch-space$",
    ): "the rule group, by its file",
    (
        "roles/scratch_space/tasks/present.yml",
        r"textfile_keep_name: scratch-space$",
    ): "the textfile (scratch-space.prom)",
    (
        "roles/scratch_space/templates/scratch-space-purge.service.j2",
        r"^StateDirectory=",
    ): "/var/lib/scratch-space",
}


class RetiredNames(unittest.TestCase):
    def test_no_retired_command_is_named(self):
        found = []
        for path in tracked():
            rel = str(path.relative_to(ROOT))
            if rel == "tests/cluster-tools/test_retired_names.py" or not path.is_file() or path.is_symlink():
                continue
            try:
                text = path.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if WORD.search(line) and not any(f == rel and re.search(p, line) for f, p in ALLOWED):
                    found.append(f"{rel}:{number}: {line.strip()[:100]}")
        self.assertEqual(found, [], "name usage and its subcommand instead (cluster_tools.usage)")

    def test_the_words_are_found_and_the_look_alikes_not(self):
        self.assertTrue(WORD.search("run `disk-usage` to see"))
        self.assertTrue(WORD.search("gpu-usage JOB"))
        for line in ("#gpu-usage", "scratch-space.yml", "scratch-space-purge", "roles/scratch-space"):
            self.assertFalse(WORD.search(line), line)


if __name__ == "__main__":
    unittest.main()
