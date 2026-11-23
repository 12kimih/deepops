"""slurm_timer_problems: Slurm's timers that must agree, checked on a rendered slurm.conf.

    {{ lookup('template', slurm_conf_template) | slurm_timer_problems }}   -> [] when fine

Each timer is set on its own line, but several only work in relation to another, and
nothing in Slurm refuses a pair that disagrees: it drains nodes or aborts jobs at run
time instead. The relations here are the ones slurm.conf(5) states; an unset key takes
Slurm's documented default.

    UnkillableStepTimeout >= 5 x MessageTimeout   (else nodes drain unexpectedly)
    HealthCheckTimeout    <  HealthCheckInterval  (when the health check runs)
    CompleteWait          == 0 or >= KillWait + 2 (the two values the manual recommends)
    BatchStartTimeout     >= PrologTimeout         (a prolog inside its own limit must not
                                                    abort a batch launch or an srun)

The time a job gets to stop cleanly (KillWait) also bounds a preempted job's grace:
roles/slurm passes it to the slurm_accounting module (preempted_grace_time).
"""

import re

MESSAGE_TIMEOUT = 10  # slurm.conf(5) defaults
KILL_WAIT = 30
HEALTH_CHECK_TIMEOUT = 60
BATCH_START_TIMEOUT = 10


def _values(conf):
    out = {}
    for line in str(conf).splitlines():
        m = re.match(r"\s*([A-Za-z]+)\s*=\s*(\S+)", line)
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


def _int(values, key, default):
    raw = values.get(key.lower())
    return int(raw) if raw is not None and raw.lstrip("-").isdigit() else default


def slurm_timer_problems(conf):
    v = _values(conf)
    problems = []
    message = _int(v, "MessageTimeout", MESSAGE_TIMEOUT)
    unkillable = _int(v, "UnkillableStepTimeout", max(60, 5 * message))
    if unkillable < 5 * message:
        problems.append(
            f"UnkillableStepTimeout={unkillable} is below 5 x MessageTimeout ({5 * message}): "
            "nodes drain while a step is still being killed"
        )
    interval = _int(v, "HealthCheckInterval", 0)
    check = _int(v, "HealthCheckTimeout", HEALTH_CHECK_TIMEOUT)
    if interval > 0 and check >= interval:
        problems.append(f"HealthCheckTimeout={check} is not below HealthCheckInterval={interval}")
    kill_wait = _int(v, "KillWait", KILL_WAIT)
    complete = _int(v, "CompleteWait", 0)
    if complete and complete < kill_wait + 2:
        problems.append(f"CompleteWait={complete} is neither 0 nor at least KillWait + 2 ({kill_wait + 2})")
    prolog = _int(v, "PrologTimeout", _int(v, "PrologEpilogTimeout", 0))
    batch = _int(v, "BatchStartTimeout", BATCH_START_TIMEOUT)
    if prolog and batch < prolog:
        problems.append(
            f"BatchStartTimeout={batch} is below the prolog's limit ({prolog}): a prolog that "
            "is slow but within its limit aborts the batch launch or the srun"
        )
    return problems


class FilterModule:
    def filters(self):
        return {"slurm_timer_problems": slurm_timer_problems}
