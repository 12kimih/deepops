#!/usr/bin/env python3
"""Render roles/slurm's job_submit.lua template with a policy file, as the role does, so
the Lua tests run on exactly what deploys.

    python3 roles/slurm/tests/render_job_submit.py POLICY.yml > job_submit.lua

POLICY.yml holds slurm_job_submit_policy; the holds' trust comes from deploy_defaults
(submit_holds_max_age), as roles/slurm/tasks/controller.yml passes it. The template uses plain Jinja only (no Ansible
filters), so jinja2 renders it the same way Ansible's template module does.
"""

import sys
from pathlib import Path

import jinja2
import yaml

ROLE = Path(__file__).resolve().parents[1]
DEPLOY_DEFAULTS = ROLE.parent / "deploy_defaults" / "defaults" / "main.yml"


def render(policy):
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True, trim_blocks=True)
    template = env.from_string((ROLE / "templates" / "etc" / "slurm" / "job_submit.lua.j2").read_text())
    max_age = yaml.safe_load(DEPLOY_DEFAULTS.read_text())["submit_holds_max_age"]
    return template.render(slurm_job_submit_policy=policy, job_submit_holds_max_age=max_age)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.stdout.write(render(yaml.safe_load(Path(sys.argv[1]).read_text())["slurm_job_submit_policy"]))
