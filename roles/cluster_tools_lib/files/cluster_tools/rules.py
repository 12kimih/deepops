"""rules: every rule that warns, limits or cancels, as data, each measured for one person
by its enforcer's own code, so a person sees the numbers the enforcer acts on.

    RULES                        the rules in order: id, title, action, settings, check
    overview(user, cfg, ...)     [Row] one per rule for USER (cluster_tools.usage, the bot)

A rule's measure imports the enforcer's decision and applies it to one person: the idle
GPUs of usage_report.analyze, the levels of disk_report.past, the scratch list
scratch_space writes, the hot nodes of io_report.collect, the user slice's cgroup limits
(cluster_tools_user_slice_limits), Slurm's MaxSubmitJobs and the membership end the
submit holds apply (submit_holds.ends_holds). Nothing is worked out a second way: a rule
the enforcer changes changes here with it. tests/cluster-tools-lib/test_rules.py holds
every enforcer to an entry here and each measure to its enforcer on the same fixtures.

The thresholds are the settings the enforcers read, as the cluster_tools role renders
them for every node (CONFIG, /etc/cluster-tools/usage.json): gpu_idle, disk, scratch,
io, login, ends_dir. A part the site does not run is empty and its rule says "off".

Each Row: rule (id), title (Korean, the glossary's word), mine, limit, state (ok, warn,
act: the enforcer acts, off: not run here, ?: its source did not answer), when (what
happens next and when, Korean), check (the command that shows the detail).
Python 3.9 or newer, standard library only.
"""

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from . import emoji, slurm
from .config import require
from .exits import AT_RISK, OK, UNKNOWN

PATH = "/etc/cluster-tools/usage.json"
# A job leaving its GPUs idle, the glossary's idle_job: one noun in titles and sentences
# (owner, 2026-11-27), the usage report's word (usage_report.IDLE_JOB) and this rule's title.
IDLE_JOB = "GPU 유휴 잡"
CONFIG_KEYS = {"prometheus_url": str}
CONFIG_OPTIONAL = {
    "gpu_idle": dict,
    "disk": dict,
    "scratch": dict,
    "io": dict,
    "login": dict,
    "ssh": dict,
    "ends_dir": str,
    "warn_fraction": (int, float),
}
STATES = ("ok", "warn", "act", "off", "?")
RISKY = ("warn", "act")
# How full a limit is a warning (the queue, a login's tasks and memory, its new SSH
# sessions): the rules file's warn_fraction, cluster_user_warn_fraction, the level every
# rule warns at; this only for a file written before it carried one.
NEAR = 0.9


def near(cfg):
    return float(cfg.get("warn_fraction", NEAR))


DAY = 86400


@dataclass
class Rule:
    id: str
    title: str  # the glossary's word for it
    action: str  # notice, cancel, hold, purge, limit, refuse
    enforcer: str  # the module that acts on it
    settings: tuple  # the settings its thresholds come from
    check: str  # the command that shows the detail
    anchor: str  # its place in the user docs


@dataclass
class Row:
    rule: str
    title: str
    mine: str
    limit: str
    state: str
    when: str = ""
    check: str = ""
    detail: dict = field(default_factory=dict)


RULES = (
    Rule("gpu-idle", IDLE_JOB, "notice, cancel", "usage_report",
         ("slurm_usage_report_idle_hours", "slurm_usage_report_idle_util_threshold",
          "slurm_usage_report_cancel_after_hours", "slurm_usage_report_cancel_warn_hours"),
         "usage gpu", "policy/rules/#gpu-idle"),
    Rule("disk", "디스크 한도", "notice, hold", "disk_report",
         ("cluster_user_warn_fraction", "disk_quota_filesystems", "submit_holds_disk", "submit_holds_from_time"),
         "usage disk", "policy/rules/#disk"),
    Rule("scratch-purge", "/scratch 정리", "purge", "scratch_space",
         ("scratch_space_stale_days", "scratch_space_grace_days"), "usage disk", "policy/rules/#scratch"),
    Rule("io-heavy", "공유 스토리지 부하", "notice", "io_report",
         ("slurm_usage_report_io_share", "slurm_usage_report_io_minutes", "io_fairness_models"),
         "usage io", "policy/rules/#io"),
    Rule("login-limits", "로그인 노드 한도", "limit", "user_slice",
         ("cluster_tools_user_slice_limits",), "usage login-node", "policy/rules/#login"),
    Rule("ssh-rate", "SSH 접속 빈도", "notice, refuse", "ssh_rate",
         ("ssh_rate_refuse_sessions", "ssh_rate_window_minutes", "ssh_rate_block_minutes",
          "ssh_rate_block_max_minutes", "ssh_rate_mode", "cluster_user_warn_fraction"),
         "usage login-node", "policy/rules/#ssh-rate"),
    Rule("queued-jobs", "제출 잡 한도", "refuse", "slurm",
         ("max_submit_jobs (accounting.yml)",), "usage submit", "policy/rules/#submit"),
    Rule("membership-end", "소속 기간 종료", "hold", "submit_holds",
         ("people (ends)", "people_leave_grace_days"), "usage submit", "policy/rules/#leave"),
)  # fmt: skip
BY_ID = {r.id: r for r in RULES}


def check_note(rule_id):
    """The line a warning about RULE_ID ends with: the command that shows the person
    where they stand (every notice names it from here, so none names a command gone)."""
    return f"`{BY_ID[rule_id].check}`로 내 현황을 보실 수 있습니다."


def load_config(path=PATH):
    return require(path, CONFIG_KEYS, CONFIG_OPTIONAL)


def row(rule_id, mine, limit, state, when="", **detail):
    r = BY_ID[rule_id]
    return Row(r.id, r.title, mine, limit, state, when, r.check, detail)


def unknown(rule_id, why):
    return row(rule_id, "?", "", "?", why)


NO_PROMETHEUS = "Prometheus 주소가 없습니다"


def reason(error):
    """A short Korean reason for a source that failed, for the person reading it: never
    the exception itself (an errno, a path, a URL)."""
    if isinstance(error, slurm.Error):
        return "Slurm이 답하지 않습니다"
    if isinstance(error, PermissionError):
        return "이 계정으로는 읽을 수 없습니다"
    if isinstance(error, FileNotFoundError):
        return "읽을 파일이 이 노드에 없습니다"
    from .prometheus import Error as PrometheusError

    if isinstance(error, PrometheusError):
        return "Prometheus가 답하지 않습니다"
    return "확인하지 못했습니다"


# --- each rule, by its enforcer's own decision ------------------------------------


def gpu_idle(user, cfg, jobs_data, prom, now):
    """The caller's running GPU jobs that usage_report would call idle (analyze), and
    whether a cancel follows (cancel_after_hours)."""
    from . import usage_report

    rule = cfg.get("gpu_idle")
    if not rule:
        return row("gpu-idle", "", "", "off")
    if prom is None:
        return unknown("gpu-idle", NO_PROMETHEUS)
    window = float(rule["idle_hours"]) * 3600
    threshold = float(rule["idle_util_threshold"])
    cancel = float(rule.get("cancel_after_hours") or 0)
    running, _ = usage_report.parse_jobs(jobs_data, now)
    mine = [j for j in running if j["user"] == user and j["gpus"]]
    limit = f"{rule['idle_hours']:g}시간 사용률 {usage_report.util_at_most(threshold)}"
    if not mine:
        return row("gpu-idle", "GPU 잡 없음", limit, "ok")
    idle = usage_report.analyze(mine, usage_report.collect_gpus(prom, int(window)), int(window), threshold)
    text = f"GPU 잡 {len(mine)}개 중 {IDLE_JOB} {len(idle)}개"
    if not idle:
        return row("gpu-idle", text, limit, "ok")
    ids = ", ".join(j["label"] for j in idle)
    if cancel:
        when = f"유휴 상태로 {cancel:g}시간 더 지나면 취소 ({ids})"
        return row("gpu-idle", text, limit, "act", when, jobs=[j["id"] for j in idle])
    return row("gpu-idle", text, limit, "warn", f"알림 ({ids})", jobs=[j["id"] for j in idle])


def disk(user, cfg, prom, now):
    """The caller's worst filesystem against its levels, as disk_report's notices and
    the submit hold judge it (past, amounts)."""
    from . import disk_report

    rule = cfg.get("disk")
    if not rule:
        return row("disk", "", "", "off")
    if prom is None:
        return unknown("disk", NO_PROMETHEUS)
    data = disk_report.collect(prom, now)
    rows = [r for r in disk_report.over_warn(data) if r[2] == user] if data else []
    mine = {k: e for k, e in (data or {}).get("users", {}).items() if k[2] == user}
    if not mine:
        return row("disk", "세는 파일시스템 없음", "", "ok")
    if not rows:
        (host, fs, _), e = max(
            mine.items(),
            key=lambda kv: ((kv[1]["used"] / kv[1]["hard"]) if kv[1]["hard"] else 0, kv[1]["used"]),
        )
        limit = disk_report.size(e["hard"]) if e["hard"] else "-"
        return row("disk", f"{disk_report.where(host, fs)} {disk_report.size(e['used'])}", limit, "ok")
    place, used, state, at_hard = disk_report.describe(rows[0])
    _, what, _, level, kind, hard = disk_report.amounts(rows[0])
    limit = disk_report.size(hard) if what == "space" and hard else (f"{hard:,.0f}" if hard else "-")
    holds = rule.get("holds_on") and now >= float(rule.get("holds_from") or 0)
    if at_hard:
        when = "새 잡 제출 보류 (줄일 때까지)" if holds else "쓰기 거부"
        return row("disk", f"{place} {used}", limit, "act", when)
    return row("disk", f"{place} {used}", limit, "warn", state)


def scratch(user, cfg, prom, now):
    """What of the caller's scratch_space's last purge run listed to go, and when first:
    the run's own figures per person (scratch_space.metrics, from the plan it writes each
    list from), which every caller can read; the list itself is in the person's own
    directory, which the bot cannot open."""
    from .disk_report import series
    from .io_report import quoted

    rule = cfg.get("scratch")
    if not rule:
        return row("scratch-purge", "", "", "off")
    if prom is None:
        return unknown("scratch-purge", NO_PROMETHEUS)
    limit = f"{rule['stale_days']}일 안 씀 + {rule['grace_days']}일"
    runs = series(prom, "scratch_space_last_success_timestamp_seconds", ())
    if not runs:
        return unknown("scratch-purge", "정리가 아직 한 번도 돌지 않았습니다")
    if now - max(runs.values()) > 2 * DAY:
        return unknown("scratch-purge", "정리가 이틀 넘게 돌지 않았습니다")
    who = "{user=" + quoted(user) + "}"
    files = sum(series(prom, "scratch_space_purge_due_files" + who, ()).values())
    if not files:
        return row("scratch-purge", "지울 파일 없음", limit, "ok")
    from .render import size

    total = sum(series(prom, "scratch_space_purge_due_bytes" + who, ()).values())
    first = min(series(prom, "scratch_space_purge_next_timestamp_seconds" + who, ()).values(), default=now)
    day = time.strftime("%Y-%m-%d", time.localtime(first))
    return row(
        "scratch-purge", f"파일 {int(files):,}개 {size(total)}", limit, "act", f"{day}부터 삭제", first=day
    )


def io(user, cfg, prom):
    """Whether a job of the caller's is among the jobs io_report names on a hot node."""
    from . import io_report

    rule = cfg.get("io")
    if not rule or not rule.get("mounts"):
        return row("io-heavy", "", "", "off")
    if prom is None:
        return unknown("io-heavy", NO_PROMETHEUS)
    share, minutes = float(rule["share"]), int(rule["minutes"])
    hot = io_report.collect(prom, rule["mounts"], share, minutes, int(rule.get("top") or 3))
    limit = f"노드 NFS가 디스크의 {share:.0%} 넘게 {minutes}분"
    named = [(item, job) for item in hot for job in item["jobs"] if job[1] == user]
    if not named:
        return row("io-heavy", "알림 대상 잡 없음", limit, "ok")
    jobs = ", ".join(sorted({job[0] for _, job in named}))
    return row(
        "io-heavy", f"{named[0][0]['host']} {named[0][0]['mount']}", limit, "warn", f"알림 (잡 {jobs})"
    )


def _read(path):
    """PATH's text, or None when there is no such file. Any other failure (a file the
    caller may not read) raises: unread is unknown, never "none"."""
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def login(user, cfg, uid, cgroup_root="/sys/fs/cgroup", sample=1.0, sleep=time.sleep):
    """The caller's user slice on this host against its limits (cluster_tools_user_slice_limits):
    memory and tasks now, CPU over SAMPLE seconds."""
    from .render import size

    limits = cfg.get("login")
    if not limits:
        return row("login-limits", "", "", "off")
    base = os.path.join(cgroup_root, "user.slice", f"user-{uid}.slice")
    if not os.path.isdir(base):
        return row("login-limits", "이 노드에 세션 없음", "", "ok")
    parts, states = [], []
    mem, mem_max = _read(f"{base}/memory.current"), _read(f"{base}/memory.max")
    if mem and mem_max and mem_max != "max":
        parts.append(f"메모리 {size(int(mem))}/{size(int(mem_max))}")
        states.append(
            "act" if int(mem) >= int(mem_max) else "warn" if int(mem) >= near(cfg) * int(mem_max) else "ok"
        )
    pids, pids_max = _read(f"{base}/pids.current"), _read(f"{base}/pids.max")
    if pids and pids_max and pids_max != "max":
        parts.append(f"프로세스 {pids}/{pids_max}")
        states.append(
            "act"
            if int(pids) >= int(pids_max)
            else "warn"
            if int(pids) >= near(cfg) * int(pids_max)
            else "ok"
        )
    cpu_max = (_read(f"{base}/cpu.max") or "max").split()
    if cpu_max[0] != "max":
        quota = int(cpu_max[0]) / int(cpu_max[1])

        def usage():
            for line in (_read(f"{base}/cpu.stat") or "").splitlines():
                if line.startswith("usage_usec "):
                    return int(line.split()[1])
            return 0

        before = usage()
        sleep(sample)
        used = (usage() - before) / 1e6 / sample
        parts.append(f"CPU {used:.1f}/{quota:g}")
        states.append("warn" if used >= near(cfg) * quota else "ok")
    state = "act" if "act" in states else "warn" if "warn" in states else "ok"
    when = {"act": "한도에 닿음: 느려지거나 새 프로세스가 거부됩니다", "warn": "한도 가까움"}.get(state, "")
    return row("login-limits", ", ".join(parts) or "한도 없음", "", state, when)


def ssh(user, cfg, prom, now):
    """The caller's new SSH sessions on each login node against its limit, and any
    refusal now: what cluster_tools.ssh_rate publishes from the counts it decides by,
    which every caller can read (the bot too)."""
    from .disk_report import series
    from .io_report import quoted

    if not cfg.get("ssh"):
        return row("ssh-rate", "", "", "off")
    if prom is None:
        return unknown("ssh-rate", NO_PROMETHEUS)
    refuse = series(prom, "ssh_rate_refuse_sessions", ())
    if not refuse:
        return unknown("ssh-rate", "로그인 노드의 집계가 아직 없습니다")
    window = series(prom, "ssh_rate_window_seconds", ())
    who = "{user=" + quoted(user) + "}"
    counts = series(prom, "ssh_rate_new_sessions" + who, ())
    until = {h: v for h, v in series(prom, "ssh_rate_blocked_until_seconds" + who, ()).items() if v > now}
    host = max(refuse, key=lambda h: (h in until, counts.get(h, 0) / refuse[h]))
    n, cap = int(counts.get(host, 0)), int(refuse[host])
    limit = f"{int(window.get(host, 600) // 60)}분에 {cap}번"
    mine = f"{host[0]} {n}번" if len(refuse) > 1 else f"{n}번"
    if host in until:
        when = time.strftime("%H:%M", time.localtime(until[host])) + "까지 새 접속 거부"
        return row("ssh-rate", mine, limit, "act", when)
    warn = series(prom, "ssh_rate_warn_sessions", ())
    if n >= warn.get(host, cap):
        return row("ssh-rate", mine, limit, "warn", f"{cap}번에 이르면 새 접속 거부")
    return row("ssh-rate", mine, limit, "ok")


def queued(user, jobs_data, assoc_data, fraction=NEAR):
    """The caller's queued and running jobs against MaxSubmitJobs (an association's
    max.jobs.total), per account; array tasks count one each, as Slurm counts them."""
    counts = {}
    for job in jobs_data.get("jobs", []):
        if job.get("user_name") != user:
            continue
        state = job.get("job_state") or []
        if any(s in state for s in ("PENDING", "RUNNING", "SUSPENDED", "CONFIGURING")):
            counts[job.get("account", "")] = counts.get(job.get("account", ""), 0) + slurm.job_tasks(job)
    limits = {}
    for a in assoc_data.get("associations", []):
        if a.get("user") != user:
            continue
        total = (((a.get("max") or {}).get("jobs") or {}).get("total")) or {}
        if total.get("set") and not total.get("infinite"):
            limits[a.get("account", "")] = min(
                limits.get(a.get("account", ""), 1 << 62), int(total["number"])
            )
    if not limits:
        return row("queued-jobs", f"{sum(counts.values())}개", "제한 없음", "ok")
    account = max(limits, key=lambda acct: counts.get(acct, 0) / limits[acct])
    n, cap = counts.get(account, 0), limits[account]
    state = "act" if n >= cap else "warn" if n >= fraction * cap else "ok"
    when = {"act": "새 잡 제출 거부", "warn": "한도 가까움"}.get(state, "")
    return row("queued-jobs", f"{n}개 ({account})", f"{cap}개", state, when)


def membership(user, cfg, now, ends=None):
    """The caller's membership end, from the file only they can read (the roster's, as the
    submit holds apply it: held from the day after it ends, submit_holds.ends_holds), or
    from ENDS ({login: {ends, login_expires}}: the cluster bot's own copy of the roster's)."""
    from . import submit_holds

    if ends is not None:
        entry = ends.get(user)
    else:
        directory = cfg.get("ends_dir")
        if not directory:
            return row("membership-end", "", "", "off")
        text = _read(os.path.join(directory, user))
        entry = json.loads(text) if text else None
    if not entry:
        return row("membership-end", "끝나는 날 없음", "", "ok")
    held = submit_holds.ends_holds({user: entry}, now)
    ends = entry["ends"]
    if held:
        return row(
            "membership-end",
            f"{ends}에 끝남",
            "",
            "act",
            f"새 잡 제출 보류, 로그인 {entry['login_expires']}까지",
        )
    soon = date.fromisoformat(ends) - date.fromtimestamp(now) <= timedelta(days=14)
    return row("membership-end", f"{ends}에 끝남", "", "warn" if soon else "ok",
               f"{date.fromisoformat(ends) + timedelta(days=1)}부터 새 잡 제출 보류")  # fmt: skip


def overview(user, cfg, uid=None, prom=None, load=slurm.load, now=None, here=True, ends=None):
    """[Row] for USER, every rule in order. A source that does not answer makes its rule
    "?", never "ok". HERE: on the host whose login limits count (the bot is not). ENDS:
    the membership ends to read instead of the person's own file (the bot's)."""
    now = time.time() if now is None else now
    out = []
    try:
        jobs = load("jobs_detail")
    except Exception as e:  # noqa: BLE001 - Slurm down: its rules unknown, the others go on
        jobs = None
        slurm_error = reason(e)
    # The login limits first: their CPU sample must not count this run's own work.
    # Measured on the login node itself (its user slice): elsewhere unknown, never off,
    # and the row's check (usage login-node) says where to look.
    first = login(user, cfg, uid) if here else unknown("login-limits", "로그인 노드에서만 잴 수 있습니다")
    for rule_id, fn in (
        ("gpu-idle", lambda: gpu_idle(user, cfg, jobs, prom, now)),
        ("disk", lambda: disk(user, cfg, prom, now)),
        ("scratch-purge", lambda: scratch(user, cfg, prom, now)),
        ("io-heavy", lambda: io(user, cfg, prom)),
        ("login-limits", lambda: first),
        ("ssh-rate", lambda: ssh(user, cfg, prom, now)),
        ("queued-jobs", lambda: queued(user, jobs, load("associations"), near(cfg))),
        ("membership-end", lambda: membership(user, cfg, now, ends)),
    ):  # fmt: skip
        needs_slurm = rule_id in ("gpu-idle", "queued-jobs")
        if needs_slurm and jobs is None:
            out.append(unknown(rule_id, slurm_error))
            continue
        try:
            out.append(fn())
        except Exception as e:  # noqa: BLE001 - one source down never hides the other rules
            out.append(unknown(rule_id, reason(e)))
    return out


def as_dict(rows):
    return [asdict(r) for r in rows]


STATE_KO = {"ok": "괜찮음", "warn": "주의", "act": "조치 대상", "off": "해당 없음", "?": "알 수 없음"}


def lines(rows):
    """ROWS in Korean, one line each, for Slack (cluster_tools.rich Markdown): the cluster
    bot's /cluster usage and Home tab say what the command prints, from the same rows."""
    out = []
    for r in rows:
        rest = []  # an empty figure is left out, never a bare "-"
        if r.mine:
            rest.append(r.mine + (f" (한도: {r.limit})" if r.limit else ""))
        elif r.limit:
            rest.append(f"한도: {r.limit}")
        if r.when:
            rest.append(r.when)
        text = f"{emoji.mark(r.state)}{STATE_KO[r.state]} **{r.title}**" + (
            ": " + ", ".join(rest) if rest else ""
        )
        if r.state not in ("ok", "off"):
            text += f" `{r.check}`"
        out.append(text)
    return out


# The JSON every usage subcommand prints (--json), versioned: a key is only added within a
# schema; one renamed or removed is the next schema. tests/cluster-tools pins the keys.
SCHEMA = 1
JSON_KEYS = ("schema", "command", "user", "at", "status", "rules")
ROW_KEYS = ("rule", "title", "mine", "limit", "state", "when", "check", "detail")


def exit_status(rows):
    """0 ok, 1 when a rule is at warn or act, 3 when one is unknown and none at risk."""
    if any(r.state in RISKY for r in rows):
        return AT_RISK
    return UNKNOWN if any(r.state == "?" for r in rows) else OK


def document(command, user, rows, now, **figures):
    """The --json object of COMMAND for USER: the schema's keys, then FIGURES."""
    return {"schema": SCHEMA, "command": command, "user": user, "at": int(now),
            "status": exit_status(rows), "rules": as_dict(rows), **figures}  # fmt: skip
