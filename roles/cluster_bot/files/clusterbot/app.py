"""The cluster bot's Slack app: Socket Mode (an outbound connection, no port to open),
one handler per action. Each handler reads Slack's input, asks a feature module
(clusterbot.scratch) and posts the outcome; nothing is decided here.

    python3 -m clusterbot.app CONFIG.json

CONFIG (written by roles/cluster_bot): bot_token_file, app_token_file, channels {help,
admin}, members {Slack member ID: cluster user}, admins [member IDs], and scratch {host,
key, user, known_hosts, policy, pending, reminders, remind_days}; scratch.host empty
leaves the feature off.
"""

import json
import sys
import threading
import time
from pathlib import Path

from . import scratch_blocks as blocks
from .scratch import Scratch, SshExecutor

UNITS = {"G": 1024**3, "T": 1024**4}
REMIND_EVERY = 3600


def size_bytes(text):
    return int(text[:-1]) * UNITS[text[-1]]


def load(path):
    cfg = json.loads(Path(path).read_text())
    cfg["bot_token"] = Path(cfg["bot_token_file"]).read_text().strip()
    cfg["app_token"] = Path(cfg["app_token_file"]).read_text().strip()
    return cfg


def build(cfg, service):
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
        text = f"<@{slack_id}> {blocks.describe_request(req)}: {outcome.text}" if req else outcome.text
        client.chat_postMessage(channel=channels["help"], text=text, thread_ts=thread)
        client.chat_postMessage(channel=slack_id, text=outcome.text)

    def submit(client, slack_id, req):
        outcome = service.request(req)
        if outcome.status == "held":
            first = client.chat_postMessage(
                channel=channels["help"],
                text=f"<@{slack_id}> {blocks.describe_request(req)}: 관리자 승인 대기",
            )
            client.chat_postMessage(
                channel=channels["admin"],
                text=outcome.text,
                blocks=blocks.held(f"<@{slack_id}>", outcome.text, outcome.key),
            )
            service.note(outcome.key, slack_id=slack_id, thread_ts=first["ts"])
            client.chat_postMessage(channel=slack_id, text=outcome.text)
        else:
            tell(client, slack_id, outcome)

    @app.command("/scratch")
    def scratch_command(ack, body, client):
        ack()
        user = user_of(body["user_id"])
        if service is None:
            client.chat_postEphemeral(channel=body["channel_id"], user=body["user_id"],
                                      text="이 클러스터에는 스크래치 공간 신청이 없습니다.")  # fmt: skip
            return
        if not user:
            client.chat_postEphemeral(channel=body["channel_id"], user=body["user_id"],
                                      text="사람 명부에 이 Slack 계정이 없습니다. 관리자에게 알려 주세요.")  # fmt: skip
            return
        client.chat_postEphemeral(channel=body["channel_id"], user=body["user_id"], text="내 스크래치 공간",
                                  blocks=blocks.home(service.mine(user)))  # fmt: skip

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
            submit(client, slack_id, {"op": "release", "user": user, "id": int(action["value"])})

    @app.view("scratch.create")
    def create(ack, body, client, view):
        ack()
        v = view["state"]["values"]
        slack_id = body["user"]["id"]
        user = user_of(slack_id)
        if user:
            req = {"op": "create", "user": user, "size": size_bytes(v["size"]["v"]["selected_option"]["value"]),
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
        req = {"op": view["callback_id"].split(".")[1], "user": user, "id": int(view["private_metadata"])}
        if req["op"] == "extend":
            req["days"] = int(value["value"])
        else:
            req["size"] = size_bytes(value["selected_option"]["value"])
        submit(client, slack_id, req)

    @app.action("scratch.approve")
    @app.action("scratch.deny")
    def decide(ack, body, client, action):
        ack()
        admin = body["user"]["id"]
        if admin not in admins:
            client.chat_postEphemeral(
                channel=body["channel"]["id"], user=admin, text="관리자만 승인할 수 있습니다."
            )
            return
        if action["action_id"] == "scratch.approve":
            outcome = service.approve(action["value"], f"<@{admin}>")
        else:
            outcome = service.deny(action["value"], f"<@{admin}>")
        client.chat_update(
            channel=body["channel"]["id"], ts=body["message"]["ts"], text=outcome.text, blocks=[]
        )
        if outcome.request.get("slack_id"):
            tell(client, outcome.request["slack_id"], outcome)

    return app


def remind_once(service, client, cfg, state, now):
    """DM the owners of allocations that end within one of remind_days, once per stage."""
    by_user = {u: s for s, u in cfg["members"].items()}
    stages = sorted(cfg["scratch"]["remind_days"], reverse=True)
    live = {str(a["id"]): a for a in service.run("list") if a["state"] == "active"}
    for key in list(state):
        if key not in live:
            del state[key]
    for key, a in live.items():
        left = (a["expires"] - now) / 86400
        due = [d for d in stages if left <= d]
        if not due:
            state.pop(key, None)  # extended past every stage: remind again next time
            continue
        if state.get(key, 10**9) <= due[-1]:
            continue
        slack_id = by_user.get(a["user"])
        if slack_id:
            client.chat_postMessage(
                channel=slack_id,
                text="스크래치 공간이 곧 만료됩니다",
                blocks=blocks.reminder(a, max(1, round(left))),
            )
        state[key] = due[-1]


def main(argv=None):
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    argv = sys.argv if argv is None else argv
    cfg = load(argv[1])
    sc = cfg["scratch"]
    service = None
    if sc["host"]:
        service = Scratch(
            SshExecutor(sc["host"], sc["key"], sc["user"], sc["known_hosts"]), sc["policy"], sc["pending"]
        )
    app = build(cfg, service)

    def reminders():
        path = Path(sc["reminders"])
        while True:
            try:
                state = json.loads(path.read_text()) if path.exists() else {}
                remind_once(service, app.client, cfg, state, time.time())
                path.write_text(json.dumps(state))
            except Exception as e:  # noqa: BLE001 - one failed round must not stop the next
                print(f"clusterbot: reminders: {e}", file=sys.stderr, flush=True)
            time.sleep(REMIND_EVERY)

    if service is not None:
        threading.Thread(target=reminders, daemon=True).start()
    SocketModeHandler(app, cfg["app_token"]).start()


if __name__ == "__main__":
    main()
