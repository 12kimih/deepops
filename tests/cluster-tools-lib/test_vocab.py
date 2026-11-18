"""Unit tests for clustertools.vocab (roles/cluster_tools_lib), no cluster needed."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from clustertools import vocab  # noqa: E402


class NodeStateTests(unittest.TestCase):
    def test_classes(self):
        cases = {
            "IDLE": "idle", "IDLE+POWERED_DOWN": "idle", "MIXED": "mix", "ALLOCATED": "alloc",
            "COMPLETING": "alloc", "MIXED+DRAIN": "drain", "IDLE+DRAIN": "drain",
            "IDLE+MAINT+RESERVED": "drain", "IDLE+REBOOT_REQUESTED": "idle",
            "DOWN+NOT_RESPONDING": "down", "MIXED+NOT_RESPONDING": "down", "INVAL": "down",
            "UNKNOWN": "down", "SOMETHINGNEW": "down", "DRAIN": "drain", "INACTIVE": "down",
            "UP": "", "": "down",
        }
        for state, cls in cases.items():
            with self.subTest(state=state):
                self.assertEqual(vocab.node_class(state), cls)
                self.assertEqual(vocab.node_class(state.split("+") if state else []), cls)

    def test_every_class_is_known_and_explained(self):
        self.assertEqual(set(vocab.NODE_HELP), set(vocab.NODE_CLASSES))
        self.assertTrue(vocab.OPEN_CLASSES < set(vocab.NODE_CLASSES))

    def test_full_state(self):
        self.assertEqual(vocab.node_state(["MIXED", "DRAIN"]), "mixed+drain")

    def test_unavailable_order(self):
        cases = [(["IDLE"], None), (["IDLE", "NOT_RESPONDING"], None), (["IDLE", "MAINT"], None),
                 (["IDLE", "DRAIN"], "drained"), (["MIXED", "DRAIN"], "draining"),
                 (["COMPLETING", "DRAIN"], "draining"), (["IDLE", "FAIL", "DRAIN"], "fail"),
                 (["DOWN", "FAIL", "DRAIN"], "down")]
        for flags, label in cases:
            with self.subTest(flags=flags):
                self.assertEqual(vocab.node_unavailable(flags), label)


class ReasonTests(unittest.TestCase):
    def test_families(self):
        cases = {
            "Priority": "wait", "Resources": "wait", "QOSGrpGRES": "limit",
            "AssocGrpCpuLimit": "limit", "QOSMaxGRESPerUser": "limit", "QOSMaxGRESPerJob": "over",
            "AssocMaxWallDurationPerJobLimit": "over", "PartitionTimeLimit": "over",
            "ReqNodeNotAvail": "nodes", "Dependency": "start", "BeginTime": "start",
            "JobHeldUser": "held", "DependencyNeverSatisfied": "error", "InvalidQOS": "error",
            "QOSNotAllowed": "error", "SomethingNew": "other",
        }
        for code, family in cases.items():
            with self.subTest(code=code):
                self.assertEqual(vocab.reason_class(code), family)

    def test_every_family_has_title_codes_and_help(self):
        for family in vocab.REASON_CLASSES:
            with self.subTest(family=family):
                self.assertIn(family, vocab.REASON_TITLE)
                self.assertIn(family, vocab.REASON_CODES)
                self.assertIn(family, vocab.REASON_HELP)


if __name__ == "__main__":
    unittest.main()
