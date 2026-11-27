"""journald: how far back this host's system journal reaches, for Prometheus.

    journal-retention TEXTFILE DAYS

journald deletes its oldest file when the journal passes SystemMaxUse or SystemMaxFiles
(roles/journald), whatever its age: on a busy login host one person's session churn
and a file per user cut the system journal to 11 hours (2026-11-27), unseen until a
look back found nothing. This reads `journalctl --system --header` (each file's head
entry, small, never the entries) and writes, through write_atomic:

    journald_entries_since_seconds       when the oldest kept system entry was written
    journald_oldest_entry_seqnum         its sequence number: above 1 once journald
                                         deleted entries (a new journal starts at 1)
    journald_retention_target_seconds    DAYS, the retention the role sizes the journal for
    journald_retention_last_success_timestamp_seconds   this run

JournalRetentionShort fires when the journal reaches back less than the target although
journald already deleted entries; a young host (nothing deleted yet) is not short.
Python 3.9 or newer, standard library only.
"""

import re
import sys
import time

from . import proc
from .cli import run_main
from .exits import CANNOT_RUN
from .files import write_atomic
from .prom import key, render

TIMEOUT = 60
# journalctl --header: each file's head entry, its number and its time in microseconds
# (the hex in parentheses).
_SEQNUM = re.compile(r"^Head sequential number: \d+ \(([0-9a-f]+)\)$", re.M)
_REALTIME = re.compile(r"^Head realtime timestamp: .*\(([0-9a-f]+)\)$", re.M)


def oldest(header):
    """(Unix time, sequence number) of the oldest head entry in HEADER (journalctl
    --header), or None for a journal without entries."""
    heads = []
    for block in header.split("File path: ")[1:]:
        seq, real = _SEQNUM.search(block), _REALTIME.search(block)
        if seq and real and int(real.group(1), 16):
            heads.append((int(real.group(1), 16) / 1e6, int(seq.group(1), 16)))
    return min(heads) if heads else None


def textfile(head, days, now):
    values = {
        key("journald_retention_target_seconds"): int(days * 86400),
        key("journald_retention_last_success_timestamp_seconds"): int(now),
    }
    if head is not None:
        values[key("journald_entries_since_seconds")] = int(head[0])
        values[key("journald_oldest_entry_seqnum")] = head[1]
    return render(values)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        days = float(argv[1]) if len(argv) == 2 else None
    except ValueError:
        days = None
    if not days or days <= 0:
        print("usage: journal-retention TEXTFILE DAYS", file=sys.stderr)
        return CANNOT_RUN

    def body():
        rc, out, err = proc.outcome(["journalctl", "--system", "--header", "--no-pager"], timeout=TIMEOUT)
        if rc != 0:
            raise OSError(f"journalctl --header: {err or rc}")
        write_atomic(argv[0], textfile(oldest(out), days, time.time()))

    return run_main("journal-retention", body)


if __name__ == "__main__":
    sys.exit(main())
