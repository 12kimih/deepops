"""scopes: whether Slack granted the bot token every scope the bot needs.

    python -m cluster_bot.scopes CONFIG TEXTFILE_DIR STATE

The scopes the bot needs are its manifest's (cluster_bot.commands SCOPES), but the app's
settings in Slack decide what the token has, and they change only when the owner
imports the manifest again: a feature added since fails with missing_scope at the first
person who uses it (2026-11-27). This reads what the token was granted (auth.test's
x-oauth-scopes) and writes, for node_exporter (TEXTFILE_DIR/cluster-bot-scopes.prom):

  cluster_bot_scope_missing{scope}                1 for each scope needed and not granted
  cluster_bot_scopes_last_success_timestamp_seconds  when Slack last answered

and tells the administrators' channel once when the missing set changes: what is
missing and what to import, then, once nothing is, that it is whole again (STATE keeps
what was said). Run as root (it reads the token) by cluster-bot-scopes.service: daily,
after each boot, and at the end of every deploy of the bot (roles/textfile_keep
refresh). Slack not answering is a failed run that leaves the last answer's file.
"""

import json
import sys
import time
from pathlib import Path

from cluster_tools.files import write_atomic  # the cluster's library (PYTHONPATH, roles/cluster_bot)
from cluster_tools.slack import Error, Slack, mentioning

from . import commands

TEXTFILE = "cluster-bot-scopes.prom"


def missing(needed, granted):
    """The scopes of NEEDED that GRANTED lacks, in order."""
    return sorted(set(needed) - set(granted))


def textfile(lacking, now):
    """The metrics' text: one series per scope lacking, and when Slack answered."""
    lines = [
        "# HELP cluster_bot_scope_missing A scope the bot needs that its token was not granted (value always 1).",
        "# TYPE cluster_bot_scope_missing gauge",
        *(f'cluster_bot_scope_missing{{scope="{s}"}} 1' for s in lacking),
        "# HELP cluster_bot_scopes_last_success_timestamp_seconds When Slack last answered with the token's scopes.",
        "# TYPE cluster_bot_scopes_last_success_timestamp_seconds gauge",
        f"cluster_bot_scopes_last_success_timestamp_seconds {int(now)}",
    ]
    return "\n".join(lines) + "\n"


def message(lacking, before, manifest):
    """What the administrators are told, in English (their channel), or None: the scopes
    missing now (LACKING), one bullet each, or that none is any more (BEFORE: what was
    said last)."""
    if lacking:
        listed = "\n".join(f"- `{s}`" for s in lacking)
        return (
            f"**The bot's token lacks {len(lacking)} scopes**\n{listed}\n"
            f"The features that need them fail (`missing_scope`). Put the content of `{manifest}` "
            "in the Slack app's App Manifest and reinstall the app."
        )
    if before:
        return (
            "**The bot's token has every scope it needs again** (it lacked "
            + ", ".join(f"`{s}`" for s in before)
            + ")"
        )
    return None


def run(cfg, slack, out_dir, state_path, now):
    """Ask, write the metrics, tell the administrators of a change; the scopes missing."""
    lacking = missing(commands.SCOPES, slack.granted_scopes())
    write_atomic(Path(out_dir) / TEXTFILE, textfile(lacking, now), mode=0o644)
    state = Path(state_path)
    try:
        before = json.loads(state.read_text()).get("reported") or []
    except (OSError, ValueError):
        before = []
    if lacking != before:
        text = message(lacking, before, cfg.get("manifest", ""))
        if text:
            said, blocks = mentioning(cfg.get("admin_mention", ""), text)
            slack.post(cfg["channels"]["admin"], said, blocks)
        write_atomic(state, json.dumps({"reported": lacking}) + "\n", mode=0o600)
    return lacking


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        print(__doc__.splitlines()[2].strip(), file=sys.stderr)
        return 2
    cfg = json.loads(Path(argv[0]).read_text())
    slack = Slack(Path(cfg["bot_token_file"]).read_text().strip())
    try:
        lacking = run(cfg, slack, argv[1], argv[2], time.time())
    except Error as e:
        print(f"cluster-bot-scopes: {e}", file=sys.stderr)
        return 1
    print(f"missing: {', '.join(lacking)}" if lacking else "every scope the bot needs is granted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
