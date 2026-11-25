"""What a person needs before their first login, from the cluster's settings only (the
bot's config `connect`: login_host, ssh_port, portal_url, dashboard_url, docs_url;
roles/cluster_bot cluster_bot_connect derives each from the setting that holds it).
Private: Slack only, never the public docs.

    connect_text(connect, login="<아이디>")  -> how to log in (ssh, the portal, the guide)
    join_text(connect, command)              -> the /join reply: how an account comes
                                                about, then how to log in once it has
    welcome(connect, command, login)         -> the account notice, once the account exists
    welcome_round(members, seen_path, exists, post, connect, command)
                                             -> DM each member whose account now exists,
                                                once; the first look only records

An empty fact is left out. Nothing here names a host, port or address: a test holds it.
"""

from . import commands, state


def connect_text(connect, login="<아이디>"):
    lines = []
    host, port = connect.get("login_host"), connect.get("ssh_port")
    if host:
        flag = f"-p {port} " if port else ""
        lines.append(f"*접속*: `ssh {flag}{login}@{host}` (계정 신청 때 낸 SSH 키로)")
    if connect.get("portal_url"):
        lines.append(f"*웹 포털*: {connect['portal_url']}")
    if connect.get("docs_url"):
        lines.append(f"*사용 안내*: {connect['docs_url']} (처음이라면 시작하기부터)")
    return "\n".join(lines)


def _join_help():
    return next(s.ko for s in commands.SUBCOMMANDS if s.name == "join")


def join_text(connect, command):
    """How an account comes about (the join subcommand of COMMAND), then how to log in."""
    how = (f"*계정 받기* ({_join_help()}): `{command} join` 양식에 아이디, 팀, SSH 공개 키를 냅니다. "
           "관리자가 승인해 계정이 생기면 접속 방법을 DM으로 알려 드립니다.")  # fmt: skip
    text = connect_text(connect)
    return how + ("\n" + text if text else "")


def welcome(connect, command, login):
    text = connect_text(connect, login)
    return (f"{login} 계정이 만들어졌습니다.\n" + (text + "\n" if text else "")
            + f"도움이 필요하면 `{command} help`를 보세요.")  # fmt: skip


def welcome_round(members, seen_path, exists, dms, connect, command):
    """MEMBERS ({Slack member ID: login}): DM each login whose account EXISTS(login) and
    that SEEN_PATH does not list (through DMS, a cluster_tools.slack.Slack: logged for
    the administrators), then list it. The first look (no file) only records
    the accounts there are, so a new bot does not greet everyone."""
    with state.locked(seen_path, {}) as seen:
        first = "logins" not in seen
        known = set(seen.get("logins", []))
        for slack_id, login in sorted(members.items(), key=lambda m: m[1]):
            if login in known or not exists(login):
                continue
            if not first:
                dms.dm(slack_id, welcome(connect, command, login), kind="welcome", login=login)
            known.add(login)
        seen["logins"] = sorted(known)
