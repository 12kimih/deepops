"""The bot's one slash command and its subcommands: the single table everything reads.

    SUBCOMMANDS                    (name, feature, Korean help, English help) in order
    parse(text)                    -> (subcommand, rest): "join", "", or "help" for none
    help_lines(command, features)  -> the help a person sees (also the Home tab's list)
    cluster_lines(names)           -> the cluster commands a login has (cluster_commands:
                                   the bot host's cluster_tools_commands), each with what
                                   its own definition says (COMMAND.summary_ko)
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
    Sub(
        "data",
        "data",
        "공유 데이터(데이터셋, 모델, 컨테이너 이미지) 받아 두기 신청",
        "ask for public data stored once for everyone",
    ),
    Sub("help", None, "이 안내", "this list"),
)
NAMES = tuple(s.name for s in SUBCOMMANDS)

# One Slack app for the cluster, whose bot token (slack_bot_token) every feature uses:
# this bot, the usage reports, the job notices. Its scopes are the union of what they
# call: each Web API method the repository calls (cluster_bot, cluster_tools.slack and
# the tools on it) maps to its scope here, and a test fails on a method missing here, so
# importing the manifest into the existing app never drops one a feature needs. The
# deploys call one too (roles/slack_defaults: usergroups.list), counted the same way. A
# method that needs one scope for public channels and another for private ones maps to
# both (the administrators' channel may be private).
API_SCOPES = {
    "chat.postMessage": "chat:write",
    "chat.postEphemeral": "chat:write",
    "chat.update": "chat:write",
    "chat.delete": "chat:write",  # a notice post whose notice the docs dropped (.notices)
    "auth.test": None,  # the bot's own IDs: which posts are its own (.notices)
    "bots.info": "users:read",
    "chat.getPermalink": None,  # the link of a logged DM (cluster_tools.slack): no scope
    "conversations.open": "im:write",
    "pins.add": "pins:write",
    "pins.remove": "pins:write",
    # The channel guides (cluster_bot.channels) and the latest notice's pin (.notices).
    "conversations.list": ("channels:read", "groups:read"),
    "conversations.info": ("channels:read", "groups:read"),
    # The notice posts in the announcement channel, found again and kept like notices.json.
    "conversations.history": ("channels:history", "groups:history"),
    "conversations.replies": ("channels:history", "groups:history"),
    "conversations.setPurpose": ("channels:write.topic", "groups:write.topic"),
    "conversations.setTopic": ("channels:write.topic", "groups:write.topic"),
    "conversations.canvases.create": "canvases:write",
    "canvases.edit": "canvases:write",
    "canvases.access.set": "canvases:write",
    "bookmarks.list": "bookmarks:read",
    "bookmarks.add": "bookmarks:write",
    "bookmarks.edit": "bookmarks:write",
    "bookmarks.remove": "bookmarks:write",
    "users.lookupByEmail": "users:read.email",
    "usergroups.list": "usergroups:read",
    "views.open": None,  # forms: the interactivity of a slash command, no scope
    "views.publish": None,  # the Home tab: no scope
}
SCOPES = {
    "chat:write": "post: reports, notices, the bot's replies and requests",
    "chat:write.public": "post in a channel the app was not invited to (the report channels)",
    "commands": f"the one slash command ({'/'.join(NAMES)})",
    "im:write": "open a DM to a person (notices, the bot's answers)",
    "pins:write": "pin the live ban list in the administrators' channel, and the latest notice",
    "channels:read": "find each guided channel's ID by its name, and its topic and canvas",
    "groups:read": "the same for a private channel (the administrators')",
    "channels:history": "find the bot's notice posts in the announcement channel, to edit or remove them",
    "groups:history": "the same for a private channel",
    "channels:write.topic": "set each guided channel's purpose and topic (narrower than channels:manage)",
    "groups:write.topic": "the same for a private channel",
    "canvases:write": "each guided channel's canvas: create, replace, keep read-only",
    "bookmarks:read": "see which of the bot's own bookmarks are still there",
    "bookmarks:write": "keep each guided channel's bookmarks, the bot's own only",
    "users:read": "users.lookupByEmail needs it beside users:read.email; the bot's own app (bots.info)",
    "users:read.email": "find a person's Slack member by <login>@<domain> when the roster has no ID",
    "usergroups:read": "find the administrators' user group's ID by its handle (roles/slack_defaults)",
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


def cluster_lines(names):
    """One line per cluster command a login has (NAMES: cluster_tools_commands), with
    what its definition says (cluster_tools.<name, - as _>.COMMAND.summary_ko), under a
    heading; none, no lines. The bot's help listed only its own subcommands, never sq or
    disk-usage (2026-11-27)."""
    if not names:
        return []
    import importlib

    lines = ["**로그인 노드의 명령** (`<명령> --help`로 자세히)"]
    for name in names:
        command = importlib.import_module(f"cluster_tools.{name.replace('-', '_')}").COMMAND
        lines.append(f"`{name}`: {command.summary_ko or command.summary}")
    return lines


def help_lines(command, features, footer="", cluster=()):
    """The help a person sees, one line a subcommand, then the cluster commands
    (CLUSTER: cluster_lines) and FOOTER (home_blocks.help_text: how to ask for help, with
    the help channel's example)."""
    lines = [f"`{command} {s.name}`: {s.ko}" for s in offered(features)]
    return "\n".join(lines + cluster_lines(cluster) + ([footer] if footer else []))


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
