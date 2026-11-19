"""Unit tests for clustertools.vocab (roles/cluster_tools_lib), no cluster needed."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "roles/cluster_tools_lib/files"))
from clustertools import vocab  # noqa: E402


class NodeStateTests(unittest.TestCase):
    def test_classes(self):
        cases = {
            "IDLE": "idle",
            "IDLE+POWERED_DOWN": "idle",
            "MIXED": "mix",
            "ALLOCATED": "alloc",
            "COMPLETING": "alloc",
            "MIXED+DRAIN": "drain",
            "IDLE+DRAIN": "drain",
            "IDLE+MAINTENANCE+RESERVED": "drain",
            "IDLE+REBOOT_REQUESTED": "idle",
            "DOWN+NOT_RESPONDING": "down",
            "MIXED+NOT_RESPONDING": "down",
            "INVAL": "down",
            "UNKNOWN": "down",
            "SOMETHINGNEW": "down",
            "DRAIN": "drain",
            "INACTIVE": "down",
            "UP": "",
            "": "down",
        }
        for state, cls in cases.items():
            with self.subTest(state=state):
                self.assertEqual(vocab.node_class(state), cls)
                self.assertEqual(vocab.node_class(state.split("+") if state else []), cls)

    # Slurm's JSON names for node and partition states (data_parser v0.0.44,
    # PARSER_FLAG_ARRAY(NODE_STATES) and (PARTITION_STATES) in
    # src/plugins/data_parser/v0.0.44/parsers.c), the only form the tools read.
    SLURM_STATES = frozenset(
        "INVALID UNKNOWN DOWN IDLE ALLOCATED ERROR MIXED FUTURE EXTERNAL RESERVED UNDRAIN CLOUD "
        "RESUME DRAIN COMPLETING NOT_RESPONDING POWERED_DOWN FAIL POWERING_UP MAINTENANCE "
        "REBOOT_REQUESTED REBOOT_CANCELED POWERING_DOWN DYNAMIC_FUTURE REBOOT_ISSUED PLANNED "
        "INVALID_REG POWER_DOWN POWER_UP POWER_DRAIN DYNAMIC_NORM BLOCKED INACTIVE UP".split()
    )
    # Words of scontrol's and sinfo's text output that the JSON never gives.
    TEXT_ONLY = frozenset(("FAILING", "INVAL", "DRAINING", "DRAINED"))

    def test_every_state_word_is_slurms(self):
        # A word Slurm never sends matches nothing: "MAINT" once left a node in a
        # maintenance reservation counted as schedulable.
        words = vocab._DOWN | vocab._DRAIN | set(vocab._BASE)
        self.assertEqual(sorted(words - self.SLURM_STATES - self.TEXT_ONLY), [])

    def test_every_class_is_known_and_explained(self):
        self.assertEqual(set(vocab.NODE_HELP), set(vocab.NODE_CLASSES))
        self.assertTrue(vocab.OPEN_CLASSES < set(vocab.NODE_CLASSES))

    def test_full_state(self):
        self.assertEqual(vocab.node_state(["MIXED", "DRAIN"]), "mixed+drain")

    def test_unavailable_order(self):
        cases = [
            (["IDLE"], None),
            (["IDLE", "NOT_RESPONDING"], None),
            (["IDLE", "MAINTENANCE"], None),
            (["IDLE", "DRAIN"], "drained"),
            (["MIXED", "DRAIN"], "draining"),
            (["COMPLETING", "DRAIN"], "draining"),
            (["IDLE", "FAIL", "DRAIN"], "fail"),
            (["DOWN", "FAIL", "DRAIN"], "down"),
        ]
        for flags, label in cases:
            with self.subTest(flags=flags):
                self.assertEqual(vocab.node_unavailable(flags), label)


class JobStateTests(unittest.TestCase):
    def test_codes_and_holding(self):
        self.assertEqual(vocab.job_code(["RUNNING"]), "R")
        self.assertEqual(vocab.job_code(["COMPLETED", "COMPLETING"]), "CG")
        self.assertEqual(vocab.job_code(["PENDING", "CONFIGURING"]), "CF")
        self.assertEqual(vocab.job_code(["OUT_OF_MEMORY"]), "OOM")
        self.assertTrue(vocab.job_holds(["SUSPENDED"]))
        self.assertFalse(vocab.job_holds(["COMPLETED"]))
        self.assertFalse(vocab.job_holds(["PENDING"]))

    def test_active_is_what_squeue_lists(self):
        self.assertTrue(vocab.job_active(["PENDING"]))
        self.assertTrue(vocab.job_active(["COMPLETED", "COMPLETING"]))
        self.assertFalse(vocab.job_active(["COMPLETED"]))
        self.assertFalse(vocab.job_active(["CANCELLED"]))

    def test_rank(self):
        self.assertEqual([vocab.job_rank(c) for c in ("PD", "R", "CG", "F")], [0, 1, 1, 2])


class ReasonTests(unittest.TestCase):
    def test_families(self):
        cases = {
            "Priority": "wait",
            "Resources": "wait",
            "QOSGrpGRES": "limit",
            "AssocGrpCpuLimit": "limit",
            "QOSMaxGRESPerUser": "limit",
            "QOSMaxGRESPerJob": "over",
            "AssocMaxWallDurationPerJobLimit": "over",
            "PartitionTimeLimit": "over",
            "ReqNodeNotAvail": "nodes",
            "Dependency": "start",
            "BeginTime": "start",
            "JobHeldUser": "held",
            "DependencyNeverSatisfied": "error",
            "InvalidQOS": "error",
            "QOSNotAllowed": "error",
            "SomethingNew": "other",
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


class SchedulableTests(unittest.TestCase):
    def test_one_definition_of_where_a_job_can_start(self):
        cases = {
            ("IDLE",): (True, "idle", False), ("MIXED",): (True, "mixed", False),
            ("ALLOCATED",): (True, "allocated", False), ("IDLE", "POWERED_DOWN"): (True, "idle", False),
            ("IDLE", "RESERVED"): (True, "idle", False), ("IDLE", "DRAIN"): (False, "idle", True),
            ("MIXED", "DRAIN"): (False, "mixed", True), ("IDLE", "MAINTENANCE"): (False, "idle", False),
            ("IDLE", "NOT_RESPONDING"): (False, "idle", False), ("DOWN",): (False, "down", False),
            ("FUTURE",): (False, "future", False), (): (False, "unknown", False),
        }  # fmt: skip
        for state, want in cases.items():
            got = (
                vocab.node_schedulable(list(state)),
                vocab.node_base(list(state)),
                vocab.node_drain(list(state)),
            )
            self.assertEqual(got, want, state)
        # The same answer as the class sq counts free capacity by.
        for state in cases:
            self.assertEqual(
                vocab.node_schedulable(list(state)), vocab.node_class(list(state)) in vocab.OPEN_CLASSES
            )


if __name__ == "__main__":
    unittest.main()
