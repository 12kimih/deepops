#!/usr/bin/env python3
"""Render one Slurm prolog/epilog template so the tests exercise the deployed form.

These files are Jinja templates, so they cannot be run -- or even parsed by
`bash -n` -- as they sit in the repository. Testing a hand-trimmed copy would
test something the cluster never runs, so the harness renders first and runs
the result.
"""

import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
sys.path.insert(0, str(ROOT / "tests" / "lib"))
from repo_files import tracked  # noqa: E402
import in_tree  # noqa: E402

# The repository's filters the templates use, loaded from their file: plugins/filter on
# sys.path would shadow the standard library with its secrets.py.
FilterModule = in_tree.load("plugins/filter/slurm_node.py").FilterModule


def role_defaults(*names):
    """The named settings as the roles define them (defaults and vars) (their defaults may template each
    other), so the tests render with the deployed values, never a copy of them."""
    found = {}
    for path in [*tracked("roles/*/defaults/main.yml"), *tracked("roles/*/vars/main.yml")]:
        found.update(yaml.safe_load(path.read_text()) or {})
    env = Environment(undefined=StrictUndefined, autoescape=False)  # noqa: S701 (not HTML)

    def resolve(value):
        if isinstance(value, str) and "{{" in value:
            return resolve(env.from_string(value).render(**found))
        if isinstance(value, dict):
            return {k: resolve(v) for k, v in value.items()}
        return value

    return {name: resolve(found[name]) for name in names}


# Values a deployment supplies, from the roles' defaults. A template referencing a
# variable not listed here fails loudly (StrictUndefined) instead of rendering an empty
# string and changing what is under test.
VARS = role_defaults(
    "slurm_config_dir",
    "slurm_install_prefix",
    "slurm_user_runtime_root",
    "slurm_log_files",
    "slurm_job_private_dirs",
    "deepops_system_path",
    "slurm_exclusive_run_dir",
)

SCOPES = ("job", "user")


def main():
    if len(sys.argv) not in (3, 4, 5):
        sys.exit("usage: render.py <template-path> <output-path> [job|user] [PRIVATE-DIRS]")

    template_path, output_path = sys.argv[1], sys.argv[2]
    # slurm_epilog_cleanup_scope. "job" is the role default.
    scope = sys.argv[3] if len(sys.argv) >= 4 else "job"
    if scope not in SCOPES:
        sys.exit("scope must be one of: " + ", ".join(SCOPES))
    # slurm_job_private_dirs, comma-separated ("" for none); the role default otherwise.
    values = dict(VARS)
    if len(sys.argv) == 5:
        values["slurm_job_private_dirs"] = [d for d in sys.argv[4].split(",") if d]

    # keep_trailing_newline so the rendered script ends the way the original
    # does; a missing final newline changes nothing functionally but makes
    # diffs against the template noisy.
    # autoescape stays off, and is written out rather than left to the default
    # so the reason sits where the scanner alert lands (CodeQL
    # py/jinja2/autoescape-false, CWE-79).
    #
    # The output here is a shell script written to a local file for `bash -n`
    # and for the harness to execute. It is never a web response and never
    # reaches a browser. HTML escaping would not protect it; it would corrupt
    # it -- `&&` becomes `&amp;&amp;`, `>"$victims"` becomes `&gt;&#34;...`,
    # and the rendered script stops being the one a deployment runs, which is
    # the whole point of rendering instead of testing a trimmed copy.
    #
    # The values substituted are the role defaults in VARS above, and the harness's own
    # choice of private directories, never anything read from the environment.
    env = Environment(
        loader=FileSystemLoader("/"),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        # Ansible's template module sets trim_blocks, so a block tag on a line
        # of its own leaves no blank line behind. Match it, or the rendered
        # scripts here differ from the deployed ones.
        trim_blocks=True,
        autoescape=False,
    )
    env.filters.update(FilterModule().filters())
    rendered = env.get_template(template_path.lstrip("/")).render(slurm_epilog_cleanup_scope=scope, **values)

    with open(output_path, "w") as handle:
        handle.write(rendered)


if __name__ == "__main__":
    main()
