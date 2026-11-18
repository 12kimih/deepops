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
