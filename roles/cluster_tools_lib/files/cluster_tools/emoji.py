"""emoji: every emoji the cluster shows in Slack, chosen in this one place.

An emoji earns its place where it helps the reader, nowhere else (owner, 2026-11-28):
- REPORT, before the title of a usage report (the hourly and weekly GPU reports and the
  daily disk report), so the reports stand out in the status channel.
- STATE, before a rule's state or a row that needs a look: small marks, as large circles
  stand taller than the text beside them. A state that needs nothing (ok, off, unknown)
  shows its word alone.
- ALERT, before an alert's title (roles/alertmanager/files/slack.tmpl, which states them
  too: Go cannot import this).
Nothing else: no pointer before a step, no icon before a log's title. The tests hold every
emoji the code and its messages show to ALLOWED.
Python 3.9 or newer, standard library only.
"""

REPORT = ":bar_chart:"
STATE = {"ok": "", "warn": ":small_orange_diamond:", "act": ":small_red_triangle:", "off": "", "?": ""}
ALERT = {
    "critical": ":small_red_triangle:",
    "warning": ":small_orange_diamond:",
    "resolved": ":heavy_check_mark:",
}

ALLOWED = frozenset(e for e in (REPORT, *STATE.values(), *ALERT.values()) if e)


def mark(state):
    """STATE's mark with the space after it, or "" for a state that shows none."""
    return f"{STATE[state]} " if STATE.get(state) else ""
