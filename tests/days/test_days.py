"""plugins/filter/days.py: a day read the same whether YAML made it a date or a string,
and every reading of a day setting going through it."""

import datetime
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "lib"))
import in_tree  # noqa: E402
from ansible_yaml import render  # noqa: E402

days = in_tree.load("plugins/filter/days.py")
MIDNIGHT = int(time.mktime(datetime.date(2026, 12, 9).timetuple()))


class Days(unittest.TestCase):
    def test_a_date_a_datetime_and_a_string_are_one_day(self):
        for value in (
            datetime.date(2026, 12, 9),
            datetime.datetime(2026, 12, 9, 15, 0),
            "2026-12-09",
            " 2026-12-09 ",
        ):
            with self.subTest(value=value):
                self.assertEqual(days.day(value), "2026-12-09")
                self.assertEqual(days.day_start(value), MIDNIGHT)

    def test_none_is_empty_and_garbage_is_named(self):
        self.assertEqual((days.day(""), days.day_start(None)), ("", 0))
        for bad in ("12/09/2026", 20261209, ["2026-12-09"]):
            with self.subTest(value=bad), self.assertRaisesRegex(Exception, "is not a day"):
                days.day_start(bad)

    def test_a_day_from_an_unquoted_yaml_file_as_ansible_reads_it(self):
        """2026-11-26 (ARA 345): schedule.yml's unquoted limits_from reached the holds'
        `length` as a date and failed every submit-holds deploy."""
        expr = "{{ ('limits_from: 2026-12-09' | from_yaml).limits_from | day_start }}"
        self.assertEqual(int(render(expr, {})), MIDNIGHT)


class Readers(unittest.TestCase):
    def test_a_disk_limits_day_reads_through_day_start(self):
        """Each limit's from (disk_quota_filesystems, a person's disk_quota) is when the
        kernel enforces it and the holds start (it replaced submit_holds_from)."""
        people = in_tree.load("plugins/filter/people.py")
        roster = {"kim": {"uid": 5000, "status": "active"}}
        for value, want in ((datetime.date(2026, 12, 9), MIDNIGHT), ("2026-12-09", MIDNIGHT), ("", 0)):
            with self.subTest(value=value):
                limits = people.people_disk_quotas(roster, {"/home": {"block_hard": "10T", "from": value}})
                self.assertEqual(limits["/home"]["kim"]["from"], want)

    def test_no_role_parses_a_day_in_jinja(self):
        """The one reading: to_datetime on a setting is the parser this replaced."""
        from repo_scan import tracked

        found = [str(p.relative_to(ROOT)) for p in tracked("roles", "playbooks")
                 if p.suffix in (".yml", ".yaml", ".j2") and "to_datetime(" in p.read_text()]  # fmt: skip
        self.assertEqual(found, [], "read a day with the day or day_start filter (plugins/filter/days.py)")


if __name__ == "__main__":
    unittest.main()
