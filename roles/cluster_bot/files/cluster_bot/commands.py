"""The bot's one slash command and its subcommands: the single table everything reads.

    SUBCOMMANDS                    (name, feature, Korean help, English help) in order
    parse(text)                    -> (subcommand, rest): "join", "", or "help" for none
    help_lines(command, features)  -> the help a person sees (also the Home tab's list)
    manifest(command, name)        -> the Slack app manifest (python -m cluster_bot.commands)
    SCOPES, API_SCOPES             the bot token's scopes, each with why, from the Web API
                                   methods the repository calls (a test scans for them)
    doc_lines(command)             -> one Markdown line per subcommand, for the docs

Slack slash commands are not namespaced: the most recently installed app's wins, and
/leave is Slack's own (leave the channel). So the bot has ONE command, named by the site
(cluster_bot_command, /cluster by default, so two clusters can share a workspace), and
every request is a subcommand of it. The dispatcher (cluster_bot.app), the help text,
the Home tab, the manifest and the docs all come from SUBCOMMANDS: a subcommand added
here is everywhere at once, and the tests require a handler for each.
"""

import json
import sys
from collections import namedtuple

Sub = namedtuple("Sub", "name feature ko en")

# feature: the request kind the cluster must run for the subcommand to be offered
# (cluster_bot.requests features), or None for always.
SUBCOMMANDS = (
    Sub("join", "join", "클러스터 계정 신청", "ask for a cluster account"),
    Sub("leave", "join", "계정 정리 신청 (졸업 등)", "give back your cluster account"),
    Sub(
        "unban", "unban", "로그인 실패로 차단된 IP 주소의 해제 신청", "ask to lift a ban after failed logins"
    ),
    Sub("project", "project", "공동작업 폴더 만들기 (홈 탭에서 관리)", "start a collaboration folder"),
    Sub("help", None, "이 안내", "this list"),
)
NAMES = tuple(s.name for s in SUBCOMMANDS)

# One Slack app for the cluster, whose bot token (slack_bot_token) every feature uses:
# this bot, the usage reports, the job notices. Its scopes are the union of what they
# call: each Web API method the repository calls (cluster_bot, cluster_tools.slack and
# the tools on it) maps to its scope here, and a test fails on a method missing here, so
# importing the manifest into the existing app never drops one a feature needs.
API_SCOPES = {
    "chat.postMessage": "chat:write",
    "chat.postEphemeral": "chat:write",
    "chat.update": "chat:write",
    "conversations.open": "im:write",
    "pins.add": "pins:write",
    "users.lookupByEmail": "users:read.email",
    "views.open": None,  # forms: the interactivity of a slash command, no scope
    "views.publish": None,  # the Home tab: no scope
}
SCOPES = {
    "chat:write": "post: reports, notices, the bot's replies and requests",
    "chat:write.public": "post in a channel the app was not invited to (the report channels)",
    "commands": f"the one slash command ({'/'.join(NAMES)})",
    "im:write": "open a DM to a person (notices, the bot's answers)",
    "pins:write": "pin the live ban list in the administrators' channel",
    "users:read": "users.lookupByEmail needs it beside users:read.email",
    "users:read.email": "find a person's Slack member by <login>@<domain> when the roster has no ID",
}


def parse(text):
    """(subcommand, the rest of TEXT): the first word if it names one, "help" for none
    or an unknown word (the person then sees what exists)."""
    words = (text or "").strip().split(None, 1)
    if not words:
        return "help", ""
    name = words[0].lower()
    return (name if name in NAMES else "help"), (words[1] if len(words) > 1 else "")


def offered(features):
    """The subcommands this cluster runs, by FEATURES (request kinds)."""
    return [s for s in SUBCOMMANDS if s.feature is None or s.feature in features]


def help_lines(command, features, footer=""):
    """The help a person sees, one line a subcommand, then FOOTER (home_blocks.help_text:
    how to ask for help, with the help channel's example)."""
    lines = [f"`{command} {s.name}`: {s.ko}" for s in offered(features)]
    return "\n".join(lines + ([footer] if footer else []))


def manifest(command, name="cluster-bot", extra_scopes=()):
    """The Slack app manifest: NAME (the existing app's, so an import renames nothing),
    one slash command whose usage hint lists the subcommands, every scope a feature
    needs (SCOPES) and EXTRA_SCOPES the app keeps for what is not this code's (an
    incoming webhook another tool posts through), the events and Socket Mode; every
    other setting as Slack creates an app, stated, so an import changes none."""
    return {
        "display_information": {"name": name},
        "features": {
            "app_home": {"home_tab_enabled": True, "messages_tab_enabled": True},
            "bot_user": {"display_name": name, "always_online": True},
            "slash_commands": [
                {
                    "command": command,
                    "description": "Cluster requests; help lists them",  # Slack keeps it short
                    "usage_hint": "|".join(NAMES),
                    "should_escape": False,
                }
            ],
        },
        "oauth_config": {"scopes": {"bot": sorted(set(SCOPES) | set(extra_scopes))}, "pkce_enabled": False},
        "settings": {
            "event_subscriptions": {"bot_events": ["app_home_opened"]},
            "interactivity": {"is_enabled": True},
            "socket_mode_enabled": True,
            "org_deploy_enabled": False,
            "is_mcp_enabled": False,
            "token_rotation_enabled": False,
            "app_level_token_rotation_enabled": False,
        },
    }


def doc_lines(command):
    """One Markdown line per subcommand, for the user docs."""
    return [f"- `{command} {s.name}`: {s.en}" for s in SUBCOMMANDS]


def main(argv=None):
    """python -m cluster_bot.commands manifest /COMMAND [APP NAME [EXTRA SCOPE ...]] |
    docs /COMMAND: the manifest (JSON, which Slack's manifest editor takes) or the doc
    lines."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2 or argv[0] not in ("manifest", "docs") or not argv[1].startswith("/"):
        print(
            "usage: python -m cluster_bot.commands manifest /COMMAND [APP NAME [EXTRA SCOPE ...]]"
            " | docs /COMMAND",
            file=sys.stderr,
        )
        return 2
    if argv[0] == "manifest":
        name = argv[2] if len(argv) > 2 else "cluster-bot"
        print(json.dumps(manifest(argv[1], name, argv[3:]), indent=2))
    else:
        print("\n".join(doc_lines(argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
