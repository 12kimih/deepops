"""Reminders before a deadline: a DM at each of a source's stages (days before), once
each, for every feature that has deadlines (people leaving).

    remind(sources, client, slack_of, state, now)

A Source has `stages` (days, e.g. [14, 7, 1]), `deadlines()` -> {key: (deadline in
epoch seconds, cluster user, item)} and `message(item, days_left)` -> (text, blocks).
STATE (a dict the caller keeps in a file) records per source and key the deadline, the
last stage sent and how many days were left when the deadline was first seen, so:
a stage is never sent twice; a moved deadline (an extension, a later end date) is seen
anew; a stage the deadline was already inside when first seen is skipped (whoever just
set a date ten days off knows it; a bot started late does not catch up), except the
last stage, the final word, always sent; and after a pause only the nearest stage due
is sent.
"""

import math
import sys
from dataclasses import dataclass
from typing import Callable

DAY = 86400


@dataclass(frozen=True)
class Source:
    stages: list
    deadlines: Callable
    message: Callable


def due(deadlines, stages, sent, now):
    """The keys of DEADLINES ({key: epoch seconds}) whose next stage has come, as
    (key, whole days left, stage), by SENT ({key: [deadline, stage sent or None, days
    left when first seen]}; see the module). A deadline seen for the first time (or
    moved) is recorded here; the caller records a stage once it is sent. Keys no longer
    listed leave SENT."""
    for key in set(sent) - set(deadlines):
        del sent[key]
    final = min(stages) if stages else None
    out = []
    for key, deadline in sorted(deadlines.items()):
        left = (deadline - now) / DAY
        record = sent.get(key)
        if not (isinstance(record, list) and record and record[0] == deadline):
            record = sent[key] = [deadline, None, left]
        # A record from before first sight was kept: seen long ago, every stage counts.
        first = record[2] if len(record) > 2 else math.inf
        reached = [d for d in stages if left <= d and (d < first or d == final)]
        if left <= 0 or not reached:
            continue
        stage = min(reached)
        if record[1] is not None and record[1] <= stage:
            continue
        out.append((key, math.ceil(left), stage))
    return out


def remind(sources, client, slack_of, state, now):
    """Send what is due for each source in SOURCES ({name: source}); SLACK_OF(user) is a
    cluster user's Slack member ID or None (nothing to send, the stage is passed). A
    failed message is tried again next round; one failing never stops the others."""
    for name, source in sources.items():
        try:
            items = source.deadlines()
        except Exception as e:  # noqa: BLE001 - one source's failure must not stop the rest
            print(f"cluster-bot: {name} reminders: {e}", file=sys.stderr, flush=True)
            continue
        sent = state.setdefault(name, {})
        for key, left, stage in due({k: v[0] for k, v in items.items()}, source.stages, sent, now):
            deadline, user, item = items[key]
            try:
                slack_id = slack_of(user)
                if slack_id:
                    text, blocks = source.message(item, left)
                    client.chat_postMessage(channel=slack_id, text=text, blocks=blocks)
            except Exception as e:  # noqa: BLE001 - the next round tries it again
                print(f"cluster-bot: {name} reminder {key}: {e}", file=sys.stderr, flush=True)
                continue
            sent[key][1] = stage
