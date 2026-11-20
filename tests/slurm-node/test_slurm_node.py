"""slurm_node (plugins/filter/slurm_node.py): the node states the maintenance steps
misread from `scontrol show node -o` before, read from Slurm's JSON."""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("slurm_node", ROOT / "plugins" / "filter" / "slurm_node.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
FIELDS = ("partitions", "cpus", "alloc_cpus", "effective_cpus", "real_memory", "alloc_memory")
FIELDS += ("specialized_memory", "gres", "tres", "tres_used", "reason_set_by_user", "reason_changed_at")


def reply(*state, reason="", started=1795120699):
    node = {f: "" for f in FIELDS}
    node.update(name="n1", state=list(state), reason=reason)
    node["slurmd_start_time"] = {"set": True, "infinite": False, "number": started}
    return json.dumps({"meta": {}, "errors": [], "nodes": [node]})


class SlurmNode(unittest.TestCase):
    def test_a_drained_node_still_running_jobs_is_busy(self):
        node = mod.slurm_node(reply("MIXED", "DRAIN"))
        self.assertEqual((node["drain"], node["busy"], node["state"]), (True, True, "mixed+drain"))
        self.assertTrue(mod.slurm_node(reply("IDLE", "DRAIN", "COMPLETING"))["busy"])
        self.assertFalse(mod.slurm_node(reply("IDLE", "DRAIN"))["busy"])

    def test_a_node_slurm_does_not_hear_from(self):
        node = mod.slurm_node(reply("DOWN", "DRAIN", "NOT_RESPONDING", reason="maintenance : Not responding"))
        self.assertEqual((node["down"], node["drain"], node["responding"]), (True, True, False))
        self.assertEqual(node["reason"], "maintenance : Not responding")
        self.assertEqual(node["slurmd_started"], 1795120699)

    def test_an_invalid_registration_is_the_flag_not_a_word_in_the_reason(self):
        self.assertTrue(mod.slurm_node(reply("IDLE", "DRAIN", "INVALID_REG"))["invalid"])
        self.assertFalse(mod.slurm_node(reply("IDLE", "DRAIN", reason="RMA in progress"))["invalid"])

    def test_powered_down_is_not_down(self):
        self.assertFalse(mod.slurm_node(reply("IDLE", "POWERED_DOWN"))["down"])

    def test_a_node_slurm_does_not_know(self):
        self.assertEqual(mod.slurm_node('{"meta": {}, "errors": [], "nodes": []}'), {"found": False})

    def test_an_error_slurm_reports_is_raised(self):
        with self.assertRaises(mod.AnsibleFilterError):
            mod.slurm_node('{"meta": {}, "errors": [{"description": "Unable to contact slurm controller"}]}')


if __name__ == "__main__":
    unittest.main()
