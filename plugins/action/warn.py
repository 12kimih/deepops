"""warn: a warning the run shows (and ARA records) without failing or changing anything,
for what a deploy cannot do yet and the operator must know: a step whose prerequisite
another playbook installs (roles/textfile_keep, tasks_from: refresh).

    - warn:
        msg: os-updates-metrics.components.service is not on this host yet: run os-updates.yml
"""

from __future__ import annotations

from ansible.plugins.action import ActionBase


class ActionModule(ActionBase):
    _requires_connection = False

    def run(self, tmp=None, task_vars=None):
        del tmp
        result = super().run(task_vars=task_vars)
        msg = str(self._task.args.get("msg") or "").strip()
        if not msg:
            return {**result, "failed": True, "msg": "warn needs msg"}
        return {**result, "changed": False, "msg": msg, "warnings": [msg]}
