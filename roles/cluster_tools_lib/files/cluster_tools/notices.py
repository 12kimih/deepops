"""notices: when a person is told again, for every tool that tells people something.

    keys = due(current, told, now, repeat, urgent=(), leeway=0)
    ... tell each of keys; once a notice reached them:  told[key] = now

TOLD ({key: Unix time of the last notice}) is the caller's, kept in its state file; a
key is a person, or a person and what they are told about. A key in CURRENT is due when
it was never told, when REPEAT seconds have passed since its last notice, or when it is
in URGENT (told every run: a state that hurts them now). LEEWAY lets a notice go that
much early, for a timer that runs a little earlier some days, so a daily run's weekly
notice never slips a day. Keys no longer current leave TOLD: someone who comes back is
told at once. Python 3.9 or newer, standard library only.
"""


def told_at(told, key):
    """KEY's last notice in TOLD, or None (never, or a damaged entry)."""
    at = told.get(key)
    return at if isinstance(at, (int, float)) and not isinstance(at, bool) else None


def due(current, told, now, repeat, urgent=(), leeway=0):
    """The keys of CURRENT (in its order) to tell this run, by TOLD; see the module."""
    current = list(current)
    for key in set(told) - set(current):
        del told[key]
    out = []
    for key in current:
        at = told_at(told, key)
        if key in urgent or at is None or now - at >= repeat - leeway:
            out.append(key)
    return out
