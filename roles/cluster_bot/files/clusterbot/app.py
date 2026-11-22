"""The cluster bot's Slack app: Socket Mode (an outbound connection, no port to open),
one handler per action. Each handler reads Slack's input, asks a feature module
(clusterbot.scratch) and posts the outcome; nothing is decided here.

    python3 -m clusterbot.app CONFIG.json

CONFIG (written by roles/cluster_bot): bot_token_file, app_token_file, channels {help,
admin}, members {Slack member ID: cluster user}, admins [member IDs], pending (the held
requests of every feature, clusterbot.requests), reminders (what went out,
clusterbot.reminders), ssh {key, user, known_hosts} (clusterbot.remote, to the hosts
it acts on), scratch {host, policy, remind_days} (host empty leaves it off), bans
{hosts {name: address}} (none leaves unban requests off), leave {remind_days, leaving
(people_leaving)}.
"""

import json
import sys
import threading
import time
from pathlib import Path

from . import accounts_blocks, bans_blocks, home_blocks, reminders, state
from . import blocks as shared
from . import scratch_blocks as blocks
from .accounts import JoinFeature, LeaveFeature, leaving_deadlines
from .bans import SERVICE as BANS
from .bans import UnbanFeature, address
from .prefs import Prefs
from .remote import Remote
from .requests import Engine
from .scratch import SERVICE as SCRATCH
from .scratch import ScratchFeature

UNITS = {"G": 1024**3, "T": 1024**4}
REMIND_EVERY = 3600


def size_bytes(text):
    return int(text[:-1]) * UNITS[text[-1]]


def load(path):
    cfg = json.loads(Path(path).read_text())
    cfg["bot_token"] = Path(cfg["bot_token_file"]).read_text().strip()
    cfg["app_token"] = Path(cfg["app_token_file"]).read_text().strip()
    return cfg


def build(cfg, engine, scratch, join=None, unban=None):
    from slack_bolt import App  # the venv's; imported here so the logic tests need no Slack

    app = App(token=cfg["bot_token"])
    members, admins, channels = cfg["members"], set(cfg["admins"]), cfg["channels"]
    policy = cfg["scratch"]["policy"]

    def user_of(slack_id):
        return members.get(slack_id)

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
            first = client.chat_postMessage(
                channel=channels["help"],
                text=f"<@{slack_id}> {engine.describe(req)}: 관리자 승인 대기",
            )
            client.chat_postMessage(
                channel=channels["admin"],
                text=outcome.text,
                blocks=shared.held(f"<@{slack_id}>", outcome.text, outcome.key),
            )
            engine.note(outcome.key, slack_id=slack_id, thread_ts=first["ts"])
            client.chat_postMessage(channel=slack_id, text=outcome.text)
        else:
            tell(client, slack_id, outcome)

    prefs = Prefs(cfg["prefs_file"])

    def publish_home(client, slack_id):
        login = user_of(slack_id)
        if not login:
            return
        allocations = scratch.mine(login) if scratch else []
        client.views_publish(
            user_id=slack_id, view=home_blocks.home(login, allocations, prefs.muted(login), engine.features)
        )

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

    @app.command("/join")
    def join_command(ack, body, client):
        ack()
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

    @app.command("/leave")
    def leave_command(ack, body, client):
        ack()
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

    @app.command("/unban-request")
    def unban_command(ack, body, client):
        ack()
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

    @app.command("/scratch")
    def scratch_command(ack, body, client):
        ack()
        user = user_of(body["user_id"])
        if scratch is None:
            client.chat_postEphemeral(channel=body["channel_id"], user=body["user_id"],
                                      text="이 클러스터에는 스크래치 공간 신청이 없습니다.")  # fmt: skip
            return
        if not user:
            client.chat_postEphemeral(channel=body["channel_id"], user=body["user_id"],
                                      text="사람 명부에 이 Slack 계정이 없습니다. 관리자에게 알려 주세요.")  # fmt: skip
            return
        client.chat_postEphemeral(channel=body["channel_id"], user=body["user_id"], text="내 스크래치 공간",
                                  blocks=blocks.home(scratch.mine(user)))  # fmt: skip

    @app.action("scratch.new")
    def new(ack, body, client):
        ack()
        client.views_open(trigger_id=body["trigger_id"], view=blocks.new_form(policy))

    @app.action("scratch.extend")
    @app.action("scratch.resize")
    def number(ack, body, client, action):
        ack()
        kind = action["action_id"].split(".")[1]
        client.views_open(trigger_id=body["trigger_id"], view=blocks.number_form(kind, action["value"]))

    @app.action("scratch.release")
    def release(ack, body, client, action):
        ack()
        slack_id = body["user"]["id"]
        user = user_of(slack_id)
        if user:
            submit(
                client,
                slack_id,
                {"kind": "scratch", "op": "release", "user": user, "id": int(action["value"])},
            )

    @app.view("scratch.create")
    def create(ack, body, client, view):
        ack()
        v = view["state"]["values"]
        slack_id = body["user"]["id"]
        user = user_of(slack_id)
        if user:
            req = {"kind": "scratch", "op": "create", "user": user, "size": size_bytes(v["size"]["v"]["selected_option"]["value"]),
                   "days": int(v["days"]["v"]["value"]), "reason": v["reason"]["v"]["value"] or ""}  # fmt: skip
            submit(client, slack_id, req)

    @app.view("scratch.extend")
    @app.view("scratch.resize")
    def change(ack, body, client, view):
        ack()
        slack_id = body["user"]["id"]
        user = user_of(slack_id)
        if not user:
            return
        value = view["state"]["values"]["v"]["v"]
        req = {"kind": "scratch", "op": view["callback_id"].split(".")[1], "user": user,
               "id": int(view["private_metadata"])}  # fmt: skip
        if req["op"] == "extend":
            req["days"] = int(value["value"])
        else:
            req["size"] = size_bytes(value["selected_option"]["value"])
        submit(client, slack_id, req)

    @app.action("request.approve")
    @app.action("request.deny")
    def decide(ack, body, client, action):
        ack()
        admin = body["user"]["id"]
        if admin not in admins:
            client.chat_postEphemeral(
                channel=body["channel"]["id"], user=admin, text="관리자만 승인할 수 있습니다."
            )
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


def reminder_sources(cfg, scratch):
    """What the bot reminds people of before a deadline (clusterbot.reminders)."""
    leave = cfg["leave"]
    sources = {
        "leave": reminders.Source(
            leave["remind_days"], lambda: leaving_deadlines(leave["leaving"]), accounts_blocks.leave_reminder
        )
    }
    if scratch is not None:
        sources["scratch"] = reminders.Source(
            cfg["scratch"]["remind_days"], scratch.deadlines, blocks.reminder
        )
    return sources


def main(argv=None):
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    argv = sys.argv if argv is None else argv
    cfg = load(argv[1])
    ssh = cfg["ssh"]

    def remote(host, service):
        return Remote(host, ssh["key"], ssh["user"], ssh["known_hosts"], service)

    features, scratch, join, unban = {}, None, None, None
    if cfg["scratch"]["host"]:
        scratch = features["scratch"] = ScratchFeature(
            remote(cfg["scratch"]["host"], SCRATCH), cfg["scratch"]["policy"]
        )
    if cfg["bans"]["hosts"]:
        hosts = {name: remote(addr, BANS) for name, addr in cfg["bans"]["hosts"].items()}
        unban = features["unban"] = UnbanFeature(hosts)
    gh = cfg.get("github") or {}
    if gh.get("repo"):
        from . import github, people_roster  # the roster module the role installs

        repo = github.Repo(github.App(gh["app_id"], gh["installation_id"], gh["key_file"]), gh["repo"])
        settings = {**cfg["roster"], "base": gh["base"]}
        join = JoinFeature(repo, people_roster, settings)
        features.update(join=join, leave=LeaveFeature(repo, people_roster, settings))
    engine = Engine(features, cfg["pending"])
    app = build(cfg, engine, scratch, join, unban)

    sources, slack_of = reminder_sources(cfg, scratch), {u: s for s, u in cfg["members"].items()}.get

    def remind_forever():
        while True:
            try:
                sent = state.read(cfg["reminders"], {})
                reminders.remind(sources, app.client, slack_of, sent, time.time())
                state.write(cfg["reminders"], sent)
            except Exception as e:  # noqa: BLE001 - one failed round must not stop the next
                print(f"clusterbot: reminders: {e}", file=sys.stderr, flush=True)
            time.sleep(REMIND_EVERY)

    threading.Thread(target=remind_forever, daemon=True).start()
    SocketModeHandler(app, cfg["app_token"]).start()


if __name__ == "__main__":
    main()
