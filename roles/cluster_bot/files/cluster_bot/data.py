"""Shared data requests as a feature of the request engine (cluster_bot.requests), and
the shared data's round: whose download ended, and the cleanup.

    feature = DataFeature(Remote(home server, ..., SERVICE), kinds, root)
    watch(feature, seen, now, unused_days, grace_days, say)   one round

Someone asks (/cluster data: what, its public https URL, its size, why); an administrator
approves (the space is one budget for everyone); the home server's service
(cluster_tools.shared_data, roles/shared_space) checks it again and downloads it as the
shared account into <root>/<kind>/<name>. req: user, category (datasets, models,
containers), name, url, size (bytes), sha256 ("" none), purpose.

Cleanup: access times over NFS say nothing of reads, so use is what people record. An
entry nobody but its requester recorded a use of for unused_days (from when it came)
gets a notice: the requester a DM with a button to keep it, the help channel a button
for anyone who uses it. A press records a use (the service's ledger) and ends the
notice; with none in grace_days, the entry is removed and the requester and the
administrators are told.
"""

import re

from .requests import Failed, Feature, Refused

# The home server's service for the bot (roles/shared_space offers it by this name).
SERVICE = "shared-data"
DAY = 86400
NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_UNITS = {"": 1, "B": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}
# A download may come out a little larger than the size asked for (it is a guess).
SLACK = 1.1


def size_of(text):
    """'120G', '1.5T', '800M' (powers of 1024) as bytes, or None."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?)(?:I?B)?\s*", (text or "").upper())
    return int(float(m.group(1)) * _UNITS[m.group(2)]) if m else None


def human(n):
    for unit in "TGM":
        if n >= _UNITS[unit]:
            return f"{n / _UNITS[unit]:.3g}{unit}"
    return f"{n}B"


def check(req, kinds):
    """{field: why} for a request's mistakes (the form shows them), {} when well formed."""
    errors = {}
    if req.get("category") not in kinds:
        errors["category"] = "종류를 고르세요"
    if not NAME.fullmatch(req.get("name") or ""):
        errors["name"] = "영문 소문자, 숫자, . _ - 로 64자까지 (첫 글자는 영문 소문자나 숫자)"
    if not (req.get("url") or "").startswith("https://"):
        errors["url"] = "공개된 https 주소만 받습니다"
    if not req.get("size"):
        errors["size"] = "크기를 120G, 1.5T처럼 적어 주세요"
    sha = req.get("sha256") or ""
    if sha and not re.fullmatch(r"[0-9a-f]{64}", sha):
        errors["sha256"] = "sha256 값(16진수 64자)이거나 비워 두세요"
    return errors


class DataFeature(Feature):
    def __init__(self, run, kinds, root):
        self.run, self.kinds, self.root = run, list(kinds), root

    def path(self, req):
        return f"{self.root}/{req['category']}/{req['name']}"

    def decide(self, req):
        errors = check(req, self.kinds)
        if errors:
            raise Refused("; ".join(errors.values()))
        now = self.run("list")
        entry = now["entries"].get(f"{req['category']}/{req['name']}")
        if entry and entry.get("state") != "failed":
            raise Refused(f"{self.path(req)}은(는) 이미 있습니다 (요청: {entry.get('requester')})")
        coming = sum(e.get("max_bytes", 0) for e in now["entries"].values() if e.get("state") == "fetching")
        free = now["budget"] - now["used"] - coming
        if int(req["size"] * SLACK) > free:
            raise Refused(
                f"공유 데이터 예산이 모자랍니다 (남은 {human(max(free, 0))}, 요청 {human(req['size'])})"
            )
        return (
            "admin",
            f"공유 데이터는 관리자가 승인합니다 (예산 {human(now['budget'])} 중 {human(now['used'])} 사용)",
        )

    def apply(self, req):
        self.run("fetch", req["category"], req["name"], req["url"], req.get("sha256") or "-",
                 int(req["size"] * SLACK), req["user"])  # fmt: skip
        return (f"{self.path(req)}에 내려받기를 시작했습니다. 끝나면 DM으로 알려 드립니다.",
                {"path": self.path(req)})  # fmt: skip

    def describe(self, req):
        return (f"공유 데이터 {req['category']}/{req['name']} ({human(req['size'])}, {req['url']}): "
                f"{req.get('purpose') or '목적 없음'}")  # fmt: skip


def last_use(entry):
    """When the entry was last used, as far as anyone knows: its arrival or the latest
    recorded use."""
    return max([entry.get("ended") or entry.get("asked") or 0, *(entry.get("uses") or {}).values()])


def watch(feature, seen, now, unused_days, grace_days, say):
    """One round: SAY(event, key, entry, **more) for each download that ended since the
    last round ("done", "failed"), each entry newly due for a notice ("notice", with
    removal: when it goes), and each removed after its notice ("removed"). SEEN ({told,
    notices}, the caller keeps it) is updated; the first round only learns what ended
    already. Failed when the service cannot be read: nothing changes."""
    entries = feature.run("list")["entries"]
    first = "told" not in seen
    told, notices = seen.setdefault("told", {}), seen.setdefault("notices", {})
    for key, entry in sorted(entries.items()):
        state = entry.get("state")
        if state in ("done", "failed") and told.get(key) != entry.get("ended"):
            told[key] = entry.get("ended")
            if not first:
                say(state, key, entry)
        if state != "done":
            continue
        if now - last_use(entry) < unused_days * DAY:
            notices.pop(key, None)  # used again: the notice is over
        elif key not in notices:
            notices[key] = now
            say("notice", key, entry, removal=now + grace_days * DAY)
        elif now - notices[key] >= grace_days * DAY:
            try:
                feature.run("remove", entry["kind"], entry["name"])
            except Failed as e:
                say("failed_removal", key, entry, error=str(e))
                continue
            notices.pop(key)
            say("removed", key, entry)
    for gone in set(told) - set(entries):
        told.pop(gone)
    for gone in set(notices) - set(entries):
        notices.pop(gone)
