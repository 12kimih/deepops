"""What a change does, in the shape `--diff` prints: the one way in-tree modules say it.

Ansible prints a result's "diff" under --diff, so a --check --diff preview shows what a
run would change, not only that it would. Every in-tree module that changes a host
returns one when it changes (tests/task-rules rule 21):

  diff=deepops_diff.text(path, before, after)   a file's content, before and after
  diff=deepops_diff.actions(["remove /etc/x"])  the steps, one line each, the way
                                                change_command says "would run: ..."

A secret never goes into one: name the file, never its content.
"""


def text(header, before, after):
    """BEFORE and AFTER (str) of HEADER, a path or a name."""
    return {"before_header": header, "after_header": header, "before": before, "after": after}


def actions(steps):
    """STEPS (str each) as the lines --diff prints."""
    return {"prepared": "".join(f"{step}\n" for step in steps)}
