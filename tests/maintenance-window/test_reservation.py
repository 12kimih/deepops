"""roles/maintenance_window/library/slurm_reservation.py: one scontrol command, only when
the reservation differs, so a rerun changes and posts nothing."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402

res = in_tree.load("roles/maintenance_window/library/slurm_reservation.py")

SHOWN = (
    "ReservationName=maintenance StartTime=2026-12-01T09:00:00 EndTime=2026-12-01T18:00:00 "
    "Duration=09:00:00 Nodes=gpu[1-8],master NodeCnt=9 CoreCnt=512 Features=(null) "
    "PartitionName=(null) Flags=MAINT,IGNORE_JOBS,SPEC_NODES,ALL_NODES TRES=cpu=512 "
    "Users=root Groups=(null) Accounts=(null) Licenses=(null) State=INACTIVE BurstBuffer=(null)"
)
WANT = {"name": "maintenance", "state": "present", "start": "2026-12-01T09:00", "end": "2026-12-01T18:00",
        "nodes": "ALL", "users": "root", "flags": ["MAINT", "IGNORE_JOBS"]}  # fmt: skip


class Plan(unittest.TestCase):
    def test_a_matching_reservation_needs_nothing(self):
        self.assertIsNone(res.plan(WANT, res.parse(SHOWN)))

    def test_none_is_created(self):
        self.assertEqual(
            res.plan(WANT, {}),
            ["create", "reservation", "ReservationName=maintenance", "StartTime=2026-12-01T09:00",
             "EndTime=2026-12-01T18:00", "Users=root", "Nodes=ALL", "Flags=MAINT,IGNORE_JOBS"],
        )  # fmt: skip

    def test_moving_it_updates_it(self):
        cmd = res.plan({**WANT, "end": "2026-12-01T20:00"}, res.parse(SHOWN))
        self.assertEqual(cmd[:2], ["update", "ReservationName=maintenance"])
        self.assertIn("EndTime=2026-12-01T20:00", cmd)

    def test_other_flags_or_nodes_change_it_in_place(self):
        # Deleting and creating it again would leave no window for a moment.
        cmd = res.plan({**WANT, "flags": ["MAINT"]}, res.parse(SHOWN))
        self.assertEqual(cmd[0], "update")
        self.assertIn("Flags=MAINT", cmd)
        cmd = res.plan({**WANT, "nodes": "gpu3"}, res.parse(SHOWN))
        self.assertEqual(cmd[0], "update")
        self.assertIn("Nodes=gpu3", cmd)

    def test_cancelling(self):
        self.assertEqual(
            res.plan({**WANT, "state": "absent"}, res.parse(SHOWN)), ["delete", "ReservationName=maintenance"]
        )
        self.assertIsNone(res.plan({**WANT, "state": "absent"}, {}))


if __name__ == "__main__":
    unittest.main()
