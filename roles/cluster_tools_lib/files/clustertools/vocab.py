"""Slurm's own words for node states and pending reasons, in one place.

Every tool classifies through these functions, so a state or a reason reads the same
in sq, qos and the usage report. Python 3.9 or newer, standard library only.
"""
from fnmatch import fnmatchcase


def _flags(state):
    """A node or partition state as an upper-case flag list. Slurm's JSON gives a list
    (["MIXED", "DRAIN"]), its text output a joined string (MIXED+DRAIN)."""
    if isinstance(state, str):
        state = state.split("+")
    return [f.upper() for f in state or [] if f] or ["UNKNOWN"]


# --- Node states --------------------------------------------------------------------
# Two levels, both Slurm's (sinfo(1), NODE STATE CODES): the full state as Slurm
# reports it, and the five base words sinfo users already know. The flag that decides
# availability wins over occupancy, and flags are compared whole, so POWERED_DOWN is
# never read as DOWN.

NODE_CLASSES = ("idle", "mix", "alloc", "drain", "down")
OPEN_CLASSES = frozenset(("idle", "mix", "alloc"))   # still take new work

_DOWN = frozenset(("DOWN", "FAIL", "FAILING", "NOT_RESPONDING", "INVALID_REG", "INVALID",
                   "FUTURE", "INVAL", "UNKNOWN", "ERROR", "INACTIVE"))
_DRAIN = frozenset(("DRAIN", "DRAINING", "DRAINED", "MAINT", "REBOOT_ISSUED"))
_BASE = {"IDLE": "idle", "MIXED": "mix", "ALLOCATED": "alloc", "COMPLETING": "alloc",
         "BLOCKED": "alloc"}

NODE_HELP = {
    "idle": "no jobs; takes work (a powered-down cloud node counts)",
    "mix": "running jobs, with room for more",
    "alloc": "fully allocated",
    "drain": "closed to new jobs by an admin (drained, draining, maintenance, rebooting)",
    "down": "unusable: down, not responding, failed, or registered wrongly",
}


def node_class(state):
    """One of NODE_CLASSES. A partition maps the same way: UP to "", DRAIN to drain,
    DOWN and INACTIVE to down. A state never met before is down: it is never offered
    as capacity."""
    flags = _flags(state)
    if flags == ["UP"]:
        return ""
    if _DOWN & set(flags):
        return "down"
    if _DRAIN & set(flags):
        return "drain"
    return _BASE.get(flags[0], "down")


def node_state(state):
    """The full state, lower case and joined as scontrol prints it: mixed+drain."""
    return "+".join(f.lower() for f in _flags(state))


def node_unavailable(state):
    """What has taken a node out, for a list of unavailable nodes: down, fail, draining
    or drained; None for a node that is up. Only an explicit DOWN, FAIL or DRAIN counts,
    in that order, so a node that is merely slow to answer is not listed. A draining
    node still runs jobs, a drained one is empty."""
    flags = set(_flags(state))
    if "DOWN" in flags:
        return "down"
    if "FAIL" in flags:
        return "fail"
    if "DRAIN" in flags:
        return "draining" if flags & {"ALLOCATED", "MIXED", "COMPLETING"} else "drained"
    return None


# --- Job states -------------------------------------------------------------------
# squeue's short codes for the JSON state names. A job's JSON state is a list: the base
# state, then flags such as COMPLETING or CONFIGURING, which squeue reports instead.

JOB_CODES = {
    "PENDING": "PD", "RUNNING": "R", "SUSPENDED": "S", "COMPLETING": "CG", "COMPLETED": "CD",
    "CONFIGURING": "CF", "CANCELLED": "CA", "FAILED": "F", "TIMEOUT": "TO", "PREEMPTED": "PR",
    "NODE_FAIL": "NF", "BOOT_FAIL": "BF", "DEADLINE": "DL", "OUT_OF_MEMORY": "OOM",
    "STOPPED": "ST", "RESIZING": "RS", "REQUEUED": "RQ", "REQUEUE_HOLD": "RH",
    "REQUEUE_FED": "RF", "RESV_DEL_HOLD": "RD", "SPECIAL_EXIT": "SE", "SIGNALING": "SI",
    "STAGE_OUT": "SO", "REVOKED": "RV",
}
# States that still own their allocation. A finished job keeps a stale allocation
# until the controller forgets it, so counting every allocation over-counts.
_HOLDS = frozenset(("RUNNING", "COMPLETING", "CONFIGURING", "RESIZING", "SIGNALING",
                    "SUSPENDED", "STOPPED", "STAGE_OUT"))
_FINISHED = frozenset(("COMPLETED", "CANCELLED", "FAILED", "TIMEOUT", "PREEMPTED", "NODE_FAIL",
                       "BOOT_FAIL", "DEADLINE", "OUT_OF_MEMORY", "REVOKED"))


def job_code(state):
    """The squeue code for a job's JSON state list: CG and CF win over the base state."""
    flags = [f.upper() for f in state or []] or ["UNKNOWN"]
    for flag in ("COMPLETING", "CONFIGURING"):
        if flag in flags:
            return JOB_CODES[flag]
    return JOB_CODES.get(flags[0], flags[0])


def job_holds(state):
    """Does a job in this state still own its allocation?"""
    return bool(_HOLDS & {f.upper() for f in state or []})


def job_active(state):
    """Would squeue list it by default: not finished, or still completing."""
    flags = {f.upper() for f in state or []}
    return "COMPLETING" in flags or not (flags & _FINISHED)


def job_rank(code):
    """Queue order of a state code: pending 0, holding resources 1, the rest 2."""
    if code == "PD":
        return 0
    return 1 if code in ("R", "CG", "S", "ST", "CF", "RS", "SI", "SO") else 2


# --- Pending reasons ----------------------------------------------------------------
# Families after the grouping in Slurm's job_reason_codes page. The code itself is
# what a tool shows: it is what squeue, sacct and the documentation use. The rules are
# tried in order, most specific first.

REASON_CLASSES = ("wait", "limit", "over", "nodes", "start", "held", "error")

_REASON_RULES = (
    ("wait", ("Priority", "Resources", "None", "Prolog", "Licenses", "")),
    ("error", ("*NeverSatisfied*", "*Invalid*", "*NotAllowed*", "BadConstraints",
               "JobLaunchFailure", "launch*")),
    ("over", ("*PerJob*", "*PerNode*", "QOSMin*", "AssocMin*", "PartitionTimeLimit",
              "PartitionNodeLimit", "PartitionConfig")),
    ("limit", ("QOSGrp*", "AssocGrp*", "QOSMax*", "AssocMax*", "Max*PerAccount",
               "Max*PerUser", "*JobsLimit", "AccountingPolicy")),
    ("nodes", ("ReqNodeNotAvail", "Reservation*", "PartitionDown", "PartitionInactive",
               "NodeDown", "Nodes_required_for_job_are_DOWN*", "PowerUpNodes")),
    ("start", ("Dependency", "BeginTime")),
    ("held", ("JobHeld*",)),
)

REASON_TITLE = {
    "wait": "waiting its turn", "limit": "limit reached", "over": "request over a limit",
    "nodes": "nodes unavailable", "start": "start condition", "held": "held",
    "error": "cannot run", "other": "other",
}
REASON_CODES = {
    "wait": "Priority, Resources",
    "limit": "QOSGrp*, AssocGrp*, QOSMax*PerUser",
    "over": "QOSMax*PerJob, AssocMax*PerJob, PartitionTimeLimit",
    "nodes": "ReqNodeNotAvail, Reservation, PartitionDown",
    "start": "Dependency, BeginTime",
    "held": "JobHeldUser, JobHeldAdmin",
    "error": "DependencyNeverSatisfied, Invalid*, BadConstraints",
}
REASON_HELP = {
    "wait": "higher-priority jobs are ahead, or the resources are in use; it starts on its own",
    "limit": "a QoS or account total is used up; it starts once other jobs under it finish (see qos)",
    "over": "the request exceeds a per-job limit; it will not start until the request changes",
    "nodes": "the nodes it needs are down, drained or reserved",
    "start": "waiting for another job (Dependency) or a start time (BeginTime)",
    "held": "held by its owner or an admin; scontrol release <jobid> lets it run",
    "error": "cannot run as submitted; scontrol show job <jobid> says why",
}


def reason_class(code):
    """The family of a pending reason code, or "other"."""
    code = code or ""
    for family, patterns in _REASON_RULES:
        if any(fnmatchcase(code, p) for p in patterns):
            return family
    return "other"
