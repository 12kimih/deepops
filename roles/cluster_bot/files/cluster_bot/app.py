"""The cluster bot's Slack app: Socket Mode (an outbound connection, no port to open),
one handler per action. Each handler reads Slack's input, asks a feature module
(cluster_bot.accounts, .bans, .projects) and posts the outcome; nothing is decided here.

    python3 -m cluster_bot.app CONFIG.json

CONFIG (written by roles/cluster_bot): bot_token_file, app_token_file, channels {help,
admin}, members {Slack member ID: cluster user}, admins [member IDs], pending (the held
requests of every feature, cluster_bot.requests), reminders (what went out,
cluster_bot.reminders), ssh {key, user, known_hosts} (cluster_bot.remote, to the hosts
it acts on), bans
{hosts {name: address}, seen} (no hosts leaves unban requests and the ban watch off),
leave {remind_days, leaving (people_leaving)}, command (the one slash command, its
subcommands cluster_bot.commands').
"""

import json
import sys
import threading
import time
from pathlib import Path

from . import accounts_blocks, bans_blocks, commands, home_blocks, projects_blocks, reminders, state
from . import blocks as shared
from .accounts import JoinFeature, LeaveFeature, leaving_deadlines
from .bans import SERVICE as BANS
from .bans import UnbanFeature, address, watch
from .prefs import Prefs
from .projects import ProjectFeature, notify_deployed
from .remote import Remote
from .requests import Engine

REMIND_EVERY = 3600
BAN_WATCH_EVERY = 300


def load(path):
    cfg = json.loads(Path(path).read_text())
    cfg["bot_token"] = Path(cfg["bot_token_file"]).read_text().strip()
    cfg["app_token"] = Path(cfg["app_token_file"]).read_text().strip()
    return cfg


def build(cfg, engine, join=None, unban=None, project=None):
    from slack_bolt import App  # the venv's; imported here so the logic tests need no Slack

    app = App(token=cfg["bot_token"])
    members, admins, channels = cfg["members"], set(cfg["admins"]), cfg["channels"]

    def user_of(slack_id):
        return members.get(slack_id)

    slack_of = {login: slack_id for slack_id, login in members.items()}.get
    projects = (cfg.get("projects") or {}).get("deployed") or {}
    projects_root = (cfg.get("projects") or {}).get("root", "")

    def tell(client, slack_id, outcome):
        """The outcome: a line in the help channel (the request's thread when it has one)
        and a DM to the person who asked."""
        req = outcome.request
        thread = req.get("thread_ts")
        text = f"<@{slack_id}> {engine.describe(req)}: {outcome.text}" if req else outcome.text
        client.chat_postMessage(channel=channels["help"], text=text, thread_ts=thread)
        client.chat_postMessage(channel=slack_id, text=outcome.text)

    def submit(client, slack_id, req):
        outcome = engine.submit(req)
        if outcome.status == "held":
            approvers = engine.approvers(outcome.key)
            first = client.chat_postMessage(
                channel=channels["help"],
                text=f"<@{slack_id}> {engine.describe(req)}: 승인 대기",
            )
            # Held for some people (a project's owners): each gets the buttons in a DM;
            # otherwise the administrators, in their channel.
            places = [s for s in map(slack_of, approvers) if s] or [channels["admin"]]
            for place in places:
                text, blocks = outcome.text, shared.held(f"<@{slack_id}>", outcome.text, outcome.key)
                if place == channels["admin"]:
                    text, blocks = shared.mentioning(cfg["admin_mention"], text, blocks)
                client.chat_postMessage(channel=place, text=text, blocks=blocks)
            engine.note(outcome.key, slack_id=slack_id, thread_ts=first["ts"])
            client.chat_postMessage(channel=slack_id, text=outcome.text)
        else:
            tell(client, slack_id, outcome)

    prefs = Prefs(cfg["prefs_file"])

    def publish_home(client, slack_id):
        login = user_of(slack_id)
        if not login:
            return
        view = home_blocks.home(
            login,
            prefs.muted(login),
            engine.features,
            cfg["command"],
            cfg["channels"]["help"],
            cfg.get("admin_group", ""),
        )
        if project is not None:
            view["blocks"] += projects_blocks.home(login, projects, projects_root)
        client.views_publish(user_id=slack_id, view=view)

    @app.event("app_home_opened")
    def home_opened(event, client):
        if event.get("tab") == "home":
            publish_home(client, event["user"])

    @app.action("home.notices")
    def notices(ack, body, client, action):
        ack()
        login = user_of(body["user"]["id"])
        if login:
            prefs.set_muted(login, action["value"] == "off")
            publish_home(client, body["user"]["id"])

    def join_command(body, client):
        if join is None or user_of(body["user_id"]):
            text = "이미 계정이 있습니다." if join else "이 클러스터는 Slack 계정 신청을 받지 않습니다."
            client.chat_postEphemeral(channel=body["channel_id"], user=body["user_id"], text=text)
            return
        client.views_open(trigger_id=body["trigger_id"], view=accounts_blocks.join_form(join.teams()))

    @app.view("join.submit")
    def join_submit(ack, body, client, view):
        v = view["state"]["values"]
        req = {"kind": "join", "user": v["login"]["v"]["value"].strip(), "name": v["name"]["v"]["value"].strip(),
               "account": v["team"]["v"]["selected_option"]["value"], "key": v["key"]["v"]["value"],
               "slack": body["user"]["id"]}  # fmt: skip
        ack()
        submit(client, body["user"]["id"], req)

    def leave_command(body, client):
        user = user_of(body["user_id"])
        if join is None or not user:
            client.chat_postEphemeral(
                channel=body["channel_id"], user=body["user_id"], text="정리할 계정이 없습니다."
            )
            return
        view = accounts_blocks.leave_form(user, body["user_id"] in admins, time.strftime("%Y-%m-%d"))
        client.views_open(trigger_id=body["trigger_id"], view=view)

    @app.view("leave.submit")
    def leave_submit(ack, body, client, view):
        ack()
        slack_id, values = body["user"]["id"], view["state"]["values"]
        if slack_id in admins and "login" in values:
            login = values["login"]["v"]["value"].strip()
        else:
            login = user_of(slack_id)  # anyone else may give back only their own account
        if login:
            req = {"kind": "leave", "user": login, "ends": values["ends"]["v"]["selected_date"],
                   "asked_by": user_of(slack_id) or slack_id}  # fmt: skip
            submit(client, slack_id, req)

    def unban_command(body, client):
        if unban is None or not user_of(body["user_id"]):
            text = ("사람 명부에 이 Slack 계정이 없습니다. 관리자에게 알려 주세요." if unban
                    else "이 클러스터는 Slack으로 차단 해제 신청을 받지 않습니다.")  # fmt: skip
            client.chat_postEphemeral(channel=body["channel_id"], user=body["user_id"], text=text)
            return
        client.views_open(trigger_id=body["trigger_id"], view=bans_blocks.unban_form())

    @app.view("unban.submit")
    def unban_submit(ack, body, client, view):
        v = view["state"]["values"]
        ip = address(v["ip"]["v"]["value"])
        if ip is None:
            ack(response_action="errors", errors={"ip": "IP 주소 형식이 아닙니다 (예: 203.0.113.7)"})
            return
        ack()
        slack_id = body["user"]["id"]
        user = user_of(slack_id)
        if user:
            submit(
                client,
                slack_id,
                {"kind": "unban", "user": user, "ip": ip, "reason": v["reason"]["v"]["value"] or ""},
            )

    # Collaboration folders (cluster_bot.projects): the Home tab's buttons and menu, and
    # the forms they open.
    def project_req(slack_id, op, name, **more):
        login = user_of(slack_id)
        return {"kind": "project", "op": op, "user": login, "project": name, **more} if login else None

    def logins(slack_ids):
        """The roster logins of Slack members, and the members the roster lacks."""
        found = [user_of(s) for s in slack_ids]
        return [x for x in found if x], [s for s, x in zip(slack_ids, found) if not x]

    def project_command(body, client):
        if project is None or not user_of(body["user_id"]):
            text = (
                "명부에 이 Slack 계정이 없습니다."
                if project
                else "이 클러스터는 공동작업 폴더 신청을 받지 않습니다."
            )
            client.chat_postEphemeral(channel=body["channel_id"], user=body["user_id"], text=text)
            return
        client.views_open(trigger_id=body["trigger_id"], view=projects_blocks.new_form())

    def help_command(body, client):
        client.chat_postEphemeral(
            channel=body["channel_id"], user=body["user_id"],
            text=commands.help_lines(
                command, engine.features, home_blocks.help_text(channels["help"], cfg.get("admin_group", ""))
            ),
        )  # fmt: skip

    # The one slash command (cfg command, /cluster): every request is a subcommand, the
    # table cluster_bot.commands.SUBCOMMANDS names them, and each has its handler here.
    handlers = {
        "join": join_command,
        "leave": leave_command,
        "unban": unban_command,
        "project": project_command,
        "help": help_command,
    }
    command = cfg["command"]

    @app.command(command)
    def cluster_command(ack, body, client):
        ack()
        name, _ = commands.parse(body.get("text", ""))
        handlers[name](body, client)

    @app.action("project.new")
    def project_new(ack, body, client):
        ack()
        client.views_open(trigger_id=body["trigger_id"], view=projects_blocks.new_form())

    @app.action("project.join")
    def project_join(ack, body, client):
        ack()
        view = projects_blocks.join_form(projects, user_of(body["user"]["id"]))
        if view is None:
            client.chat_postMessage(channel=body["user"]["id"], text="참여를 요청할 프로젝트가 없습니다.")
            return
        client.views_open(trigger_id=body["trigger_id"], view=view)

    @app.action("project.menu")
    def project_menu(ack, body, client, action):
        ack()
        op, name = action["selected_option"]["value"].split(":", 1)
        slack_id = body["user"]["id"]
        if op == "manage":
            client.views_open(trigger_id=body["trigger_id"], view=projects_blocks.manage_form(name))
        elif op == "purpose":
            purpose = (projects.get(name) or {}).get("purpose", "")
            client.views_open(trigger_id=body["trigger_id"], view=projects_blocks.purpose_form(name, purpose))
        else:  # leave, close: the request itself (each says what it will do in its DM)
            req = project_req(slack_id, op, name)
            if req:
                submit(client, slack_id, req)

    @app.view("project.new.submit")
    def project_new_submit(ack, body, client, view):
        v = view["state"]["values"]
        name = v["name"]["v"]["value"].strip()
        picked = {
            role: logins((v[role]["v"] or {}).get("selected_users") or [])
            for role in ("owners", "members", "viewers")
        }
        missing = sorted({s for _, gone in picked.values() for s in gone})
        if missing:
            ack(
                response_action="errors",
                errors={
                    "members": "명부에 없는 Slack 계정이 있습니다: " + " ".join(f"<@{s}>" for s in missing)
                },
            )
            return
        ack()
        slack_id = body["user"]["id"]
        place = ((v.get("place") or {}).get("v", {}).get("selected_option") or {}).get("value", "home")
        req = project_req(slack_id, "new", name, purpose=(v["purpose"]["v"]["value"] or "").strip(), place=place,
                          **{role: found for role, (found, _) in picked.items()})  # fmt: skip
        if req:
            submit(client, slack_id, req)

    @app.view("project.join.submit")
    def project_join_submit(ack, body, client, view):
        ack()
        v, slack_id = view["state"]["values"], body["user"]["id"]
        req = project_req(slack_id, "join", v["project"]["v"]["selected_option"]["value"],
                          role=v["role"]["v"]["selected_option"]["value"])  # fmt: skip
        if req:
            submit(client, slack_id, req)

    @app.view("project.manage.submit")
    def project_manage_submit(ack, body, client, view):
        v = view["state"]["values"]
        login = user_of(v["person"]["v"]["selected_user"])
        if not login:
            ack(response_action="errors", errors={"person": "명부에 없는 Slack 계정입니다"})
            return
        ack()
        role = v["role"]["v"]["selected_option"]["value"]
        slack_id = body["user"]["id"]
        req = project_req(
            slack_id, "set", view["private_metadata"], login=login, role=None if role == "remove" else role
        )
        if req:
            submit(client, slack_id, req)

    @app.view("project.purpose.submit")
    def project_purpose_submit(ack, body, client, view):
        ack()
        slack_id = body["user"]["id"]
        purpose = (view["state"]["values"]["purpose"]["v"]["value"] or "").strip()
        req = project_req(slack_id, "purpose", view["private_metadata"], purpose=purpose)
        if req:
            submit(client, slack_id, req)

    @app.action("request.approve")
    @app.action("request.deny")
    def decide(ack, body, client, action):
        ack()
        admin = body["user"]["id"]
        # An administrator, or one of the people the request is held for (a project's owners).
        if admin not in admins and user_of(admin) not in engine.approvers(action["value"]):
            client.chat_postMessage(channel=admin, text="이 요청을 승인할 수 있는 사람이 아닙니다.")
            return
        if action["action_id"] == "request.approve":
            outcome = engine.approve(action["value"], f"<@{admin}>")
        else:
            outcome = engine.deny(action["value"], f"<@{admin}>")
        client.chat_update(
            channel=body["channel"]["id"], ts=body["message"]["ts"], text=outcome.text, blocks=[]
        )
        if outcome.request.get("slack_id"):
            tell(client, outcome.request["slack_id"], outcome)

    return app


def reminder_sources(cfg):
    """What the bot reminds people of before a deadline (cluster_bot.reminders)."""
    leave = cfg["leave"]
    sources = {
        "leave": reminders.Source(
            leave["remind_days"], lambda: leaving_deadlines(leave["leaving"]), accounts_blocks.leave_reminder
        )
    }
    return sources


def every(seconds, name, round_):
    """ROUND_() now and then every SECONDS, on a thread of its own; a failed round is
    printed and the next one runs."""

    def loop():
        while True:
            try:
                round_()
            except Exception as e:  # noqa: BLE001 - one failed round must not stop the next
                print(f"cluster-bot: {name}: {e}", file=sys.stderr, flush=True)
            time.sleep(seconds)

    threading.Thread(target=loop, name=name, daemon=True).start()


def admin_messages(client, channel, mention=""):
    """(post, update) for messages the bot keeps in CHANNEL (cluster_bot.bans.watch): post
    returns the message as {channel ID, ts} and pins it on request (a pin Slack refuses
    leaves it unpinned, never unrecorded); a message not pinned (a notice to act on)
    starts with MENTION. update says False when the message is gone (deleted), and raises
    on any other refusal."""
    from slack_sdk.errors import SlackApiError

    def post(text, blocks, pin=False):
        if not pin:  # a notice to act on mentions the administrators; a pinned list does not
            text, blocks = shared.mentioning(mention, text, blocks)
        sent = client.chat_postMessage(channel=channel, text=text, blocks=blocks)
        message = {"channel": sent["channel"], "ts": sent["ts"]}
        if pin:
            try:
                client.pins_add(channel=message["channel"], timestamp=message["ts"])
            except SlackApiError as e:  # the message stands unpinned (the bot is not in the channel)
                print(
                    f"cluster-bot: pin in {channel}: {e.response.get('error')}", file=sys.stderr, flush=True
                )
        return message

    def update(message, text, blocks):
        try:
            client.chat_update(channel=message["channel"], ts=message["ts"], text=text, blocks=blocks)
        except SlackApiError as e:
            if e.response.get("error") in ("message_not_found", "cant_update_message"):
                return False
            raise
        return True

    return post, update


def main(argv=None):
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    argv = sys.argv if argv is None else argv
    cfg = load(argv[1])
    ssh, channels = cfg["ssh"], cfg["channels"]

    def remote(host, service):
        return Remote(host, ssh["key"], ssh["user"], ssh["known_hosts"], service)

    features, join, unban, project = {}, None, None, None
    if cfg["bans"]["hosts"]:
        hosts = {name: remote(addr, BANS) for name, addr in cfg["bans"]["hosts"].items()}
        unban = features["unban"] = UnbanFeature(hosts)
        unban.command = cfg["command"]  # the ban list names how to ask for an unban
    gh = cfg.get("github") or {}
    if gh.get("repo"):
        from . import github, people_roster  # the roster module the role installs

        repo = github.Repo(github.App(gh["app_id"], gh["installation_id"], gh["key_file"]), gh["repo"])
        settings = {**cfg["roster"], "base": gh["base"]}
        join = JoinFeature(repo, people_roster, settings)
        features.update(join=join, leave=LeaveFeature(repo, people_roster, settings))
        if cfg.get("projects"):
            p = cfg["projects"]
            project = features["project"] = ProjectFeature(
                repo,
                people_roster,
                {**settings, "gid_min": p["gid_min"], "reserved_gids": p["reserved_gids"]},
            )
    engine = Engine(features, cfg["pending"])
    app = build(cfg, engine, join, unban, project)

    sources, slack_of = reminder_sources(cfg), {u: s for s, u in cfg["members"].items()}.get

    def remind_round():
        with state.locked(cfg["reminders"], {}) as sent:
            reminders.remind(sources, app.client, slack_of, sent, time.time())

    every(REMIND_EVERY, "reminders", remind_round)
    if project is not None:  # a deploy restarts the bot: whose role changed, told once
        p = cfg["projects"]
        notify_deployed(p["deployed"], p["seen"], p["root"], slack_of,
                        lambda who, text: app.client.chat_postMessage(channel=who, text=text),
                        projects_blocks.notice)  # fmt: skip
    if unban is not None:
        post, update = admin_messages(app.client, channels["admin"], cfg["admin_mention"])

        def watch_round():
            with state.locked(cfg["bans"]["seen"], {}) as seen:
                watch(unban, post, update, seen)

        every(BAN_WATCH_EVERY, "ban watch", watch_round)
    SocketModeHandler(app, cfg["app_token"]).start()


if __name__ == "__main__":
    main()
