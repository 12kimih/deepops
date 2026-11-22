"""Reminders before a deadline: a DM at each of a source's stages (days before), once
each, for every feature that has deadlines (scratch allocations ending, people leaving).

    remind(sources, client, slack_of, state, now)

A Source has `stages` (days, e.g. [7, 1]), `deadlines()` -> {key: (deadline in epoch
seconds, cluster user, item)} and `message(item, days_left)` -> (text, blocks). STATE
(a dict the caller keeps in a file) records per source and key the deadline and the last
stage sent, so a moved deadline (an extension, a later end date) starts over, a stage
is never sent twice, and after a pause only the nearest stage due is sent.
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
    (key, whole days left, stage), by SENT ({key: [deadline, stage]}: what went out).
    Keys no longer listed leave SENT; the caller records a stage once it is sent."""
    for key in set(sent) - set(deadlines):
        del sent[key]
    out = []
    for key, deadline in sorted(deadlines.items()):
        left = (deadline - now) / DAY
        reached = [d for d in stages if left <= d]
        if left <= 0 or not reached:
            continue
        stage = min(reached)
        last = sent.get(key)
        if last and last[0] == deadline and last[1] <= stage:
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
            print(f"clusterbot: {name} reminders: {e}", file=sys.stderr, flush=True)
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
                print(f"clusterbot: {name} reminder {key}: {e}", file=sys.stderr, flush=True)
                continue
            sent[key] = [deadline, stage]
