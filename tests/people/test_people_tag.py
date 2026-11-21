"""`--tags people` applies a roster change everywhere it reaches.

A person added to people.yml must get, from one `slurm-cluster.yml --tags people` run,
everything derived from the roster: not only the account and the Slurm association but
the disk quota, the podman ID range, the chat ID the reports and the bot use. A play
that runs a role whose settings come from the roster without the people tag left those
for the next full deploy (2026-11-22: quotas, podman ranges, the Slack map). Here the
roster's consumers are found from what reads `people`, directly or through a setting
derived from it (slack_members): a role's own defaults or vars, or a setting
config.example derives from it (`#name: "{{ people | ... }}"`); every play
that runs one must carry the tag, on the play or on the role's entry, unless the role
tags its roster tasks itself.
"""

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_scan import tracked  # noqa: E402

# The roster's own roles (they define it or make the accounts, tagged by themselves) and
# the shared defaults that only name a derived setting.
OWNERS = {"people", "people_defaults", "local_accounts", "directory_defaults", "slack_defaults"}


def role_of(path):
    return Path(path).relative_to(ROOT).parts[1]


def defined_by(var):
    """The role whose defaults define VAR, or None."""
    for path in tracked("roles/*/defaults/main.yml"):
        if re.search(rf"(?m)^{re.escape(var)}:", path.read_text()):
            return role_of(path)
    return None


def settings():
    """{(role, name): value text} of every role default and var (top-level keys)."""
    out = {}
    for path in tracked("roles/*/defaults/main.yml") + tracked("roles/*/vars/main.yml"):
        for name, value in (yaml.safe_load(path.read_text()) or {}).items():
            out[(role_of(path), name)] = str(value)
    return out


def reads(text, names):
    return any(re.search(rf"\{{\{{[^}}]*\b{re.escape(n)}\b", text) for n in names)


def consumers():
    """The roles reading the roster: directly, or through a setting derived from it
    (slack_members, from people), followed until nothing new is derived."""
    values = settings()
    derived, grown = {"people"}, True
    while grown:
        new = {name for (_, name), text in values.items() if name not in derived and reads(text, derived)}
        grown, derived = bool(new), derived | new
    found = {role for (role, name), text in values.items() if reads(text, derived - {name})}
    for path in tracked("config.example/group_vars/*.yml"):
        for m in re.finditer(r"(?m)^#?(\w+):.*", path.read_text()):
            if reads(m.group(0), derived):
                role = defined_by(m.group(1))
                if role:
                    found.add(role)
    return found - OWNERS


def plays():
    """(playbook, play, role entry) for every role a play lists."""
    for path in tracked("playbooks/**/*.yml"):
        docs = yaml.safe_load(path.read_text()) or []
        for play in docs if isinstance(docs, list) else []:
            for entry in play.get("roles") or []:
                entry = entry if isinstance(entry, dict) else {"role": entry}
                yield path, play, entry


def tags_itself(role):
    """A large role tags the tasks that read the roster itself (roles/slurm: accounting,
    the users allowed onto nodes) instead of being tagged whole."""
    return any(
        re.search(r"(?m)^\s*-\s*people\s*$", path.read_text())
        for path in tracked(f"roles/{role}/tasks/*.yml")
    )


def tags(node):
    value = node.get("tags") or []
    return [value] if isinstance(value, str) else value


class PeopleTag(unittest.TestCase):
    def test_the_roster_has_consumers(self):
        self.assertTrue({"disk_quota", "podman", "slurm_usage_report", "cluster_bot"} <= consumers())

    def test_every_play_running_a_roster_consumer_carries_the_people_tag(self):
        wanted = consumers()
        missing = sorted(
            f"{path.relative_to(ROOT)}: {entry.get('role') or entry.get('name')}"
            for path, play, entry in plays()
            if (entry.get("role") or entry.get("name")) in wanted
            and "people" not in tags(play) + tags(entry)
            and not tags_itself(entry.get("role") or entry.get("name"))
        )
        self.assertEqual(missing, [], "tag the play (or the role's entry) people")


if __name__ == "__main__":
    unittest.main()
