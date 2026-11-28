"""cards: the one look of every message the cluster posts in Slack, the reports' and the
bot's alike, so a disk report, the hourly GPU status, a rule's DM and the bot's Home tab
read the same way (owner, 2026-11-27: "structured, eye-catching, easy to read").

    title(name, when, report)  -> a header block: the message's noun and its time (REPORT:
                                  a usage report, emoji.REPORT first)
    part(label, body, state)   -> rich text: a short bold label (its state's mark first), the body
    facts(pairs)               -> a section of up to ten (label, value, state) side by side
    gauges(header, rows)       -> a fixed-width table (slack.table) for a gauge column (slack.bar)
    note(text)                 -> small print: an explanation, never a list or a step
    step(text)                 -> the "what you can do" line, last; its command from cluster_tools.rules
    notice(rule_id, text)      -> (text, blocks) of a DM about a rule, titled with the rule's name

Every text goes through cluster_tools.rich; a state is one of cluster_tools.rules.STATES
and shows its mark from cluster_tools.emoji, the one place every emoji is chosen, as the
usage command's rows and the Home tab show it.
Python 3.9 or newer, standard library only.
"""

from . import emoji, rich, rules
from .slack import table

MAX_FACTS = 10  # fields of one section (Slack)


def mark(state):
    """STATE's mark ("" for none), the one the CLI's rows and the Home tab show."""
    return emoji.mark(state)


def title(name, when="", report=False):
    """The message's header: NAME, a noun, then WHEN (its date or time) if given; a usage
    REPORT's (the GPU and disk reports) with emoji.REPORT first."""
    text = f"{name}  {when}" if when else name
    return rich.header(f"{emoji.REPORT} {text}" if report else text)


def part(label, body="", state=""):
    """One part of a message: LABEL in bold (STATE's mark first), BODY (Markdown) below it."""
    head = f"{mark(state)}**{label}**"
    return rich.blocks(f"{head}\n{body}" if body else head)


def facts(pairs):
    """A section of PAIRS [(label, value, state)] side by side, each label bold above its
    value (STATE's mark before the value; state "" for none)."""
    if len(pairs) > MAX_FACTS:
        raise ValueError(f"{len(pairs)} facts: a section holds at most {MAX_FACTS}")
    return rich.fields(*(f"**{label}**\n{mark(state)}{value}" for label, value, state in pairs))


def gauges(header, rows):
    """ROWS under HEADER as one fixed-width table, so gauges (slack.bar) and figures line up."""
    return table(header, rows)


def note(text):
    """Small print under a part: what a figure means, where it comes from."""
    return rich.context(text)


def step(text):
    """The last line: what the reader can do, with the command (a rule's check, from
    cluster_tools.rules, never typed here)."""
    return rich.blocks(text)


def notice(rule_id, text):
    """(text, blocks) of a DM about RULE_ID: the rule's title as the header (the glossary's
    word, rules.RULES), then TEXT, whose last line is its step (every rule DM's text ends
    with the command that shows the person where they stand, on a line of its own). TEXT
    stays the notification's fallback and the DM log's line."""
    body, _, last = text.rpartition("\n")
    if not body:
        raise ValueError(f"a {rule_id} DM ends with its step on a line of its own: {text!r}")
    return text, [title(rules.BY_ID[rule_id].title), *rich.blocks(body), *step(last)]
