"""roles/cluster_sudoers prunes only drop-ins nothing owns: not ours, not kept, owned by
no package and recorded by no component. A component's own drop-in (the cluster bot's
sudo rule, which fail2ban.yml writes before this role runs) was pruned (2026-11-23)."""

import base64
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from ansible_yaml import load, render, same_name  # noqa: E402

TASKS = load("roles/cluster_sudoers/tasks/main.yml")
CLASSIFY = next(t for t in TASKS if same_name(t.get("name"), "Classify the sudoers drop-ins"))
DROPINS = {
    "stdout_lines": ["no deepops-admins", "no deepops-cluster-bot-access", "no stray", "yes pkg", "no README"]
}
RECORD = {"components": {}, "items": {"file:/etc/sudoers.d/deepops-cluster-bot-access": ["cluster_bot_access"],
                                       "file:/etc/elsewhere": ["x"]}}  # fmt: skip


def unmanaged(records):
    values = {"cluster_sudoers_file": "deepops-admins", "cluster_sudoers_keep": ["README", "root"],
              "cluster_sudoers_dropins": DROPINS, "cluster_sudoers_records": records}  # fmt: skip
    for name in ("cluster_sudoers_recorded", "cluster_sudoers_unmanaged_names"):  # one reads the other
        values[name] = render(CLASSIFY["vars"][name], values)
    return values["cluster_sudoers_unmanaged_names"]


class RecordedDropins(unittest.TestCase):
    def test_a_components_own_drop_in_is_never_pruned(self):
        content = base64.b64encode(json.dumps(RECORD).encode()).decode()
        self.assertEqual(unmanaged({"content": content}), ["stray"])

    def test_without_a_record_only_the_rest_is_kept(self):
        self.assertEqual(unmanaged({"failed": True}), ["deepops-cluster-bot-access", "stray"])


if __name__ == "__main__":
    unittest.main()
