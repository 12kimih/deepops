"""Days as a site writes them: the one reading of a date setting.

    {{ a_day | day }}        "2026-12-09", or "" for none
    {{ a_day | day_start }}  its local midnight as Unix time, 0 for none

A day comes from YAML: unquoted (2026-12-09) YAML reads it as a date, quoted as a
string, and a lookup of a file (schedule.yml | from_yaml) hands on what YAML read. Every
reading takes all three, so how a site quotes a day never matters: a Jinja reading that
took only a string failed every submit-holds deploy once schedule.yml's unquoted
limits_from reached it (`length` of a date, 2026-11-26). plugins/filter/people.py reads
the roster's days through here too. Anything else is an error naming it.
"""

import datetime
import time


def parse(value):
    """VALUE (a date, a datetime, "YYYY-MM-DD" or empty) as a datetime.date, or None.
    ValueError or TypeError for anything else."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return datetime.date(value.year, value.month, value.day)
    if not isinstance(value, str):
        raise TypeError(f"not a day: {value!r}")
    return datetime.date.fromisoformat(value.strip())


def _parsed(value):
    try:
        return parse(value)
    except (TypeError, ValueError) as e:
        # Imported here: the module runs without Ansible too, shipped beside the roster
        # module in the cluster bot (roles/cluster_bot), where only parse() is used.
        from ansible.errors import AnsibleFilterError

        raise AnsibleFilterError(f"{value!r} is not a day (YYYY-MM-DD): {e}") from None


def day(value):
    """VALUE as "YYYY-MM-DD", or "" for none."""
    parsed = _parsed(value)
    return parsed.isoformat() if parsed else ""


def day_start(value):
    """VALUE's local midnight (the controller's time zone, the cluster's) as Unix time,
    or 0 for none."""
    parsed = _parsed(value)
    return int(time.mktime(parsed.timetuple())) if parsed else 0


class FilterModule:
    def filters(self):
        return {"day": day, "day_start": day_start}
