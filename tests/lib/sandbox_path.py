"""A PATH for code under test that reaches only what the test provides.

A test that puts its fake commands in front of the host's PATH still reaches the host
for every command it did not fake, or where a test removes a fake to check the missing
case. On the controller that is the real thing: slurm-smd-client puts scontrol and
sacctmgr in /usr/bin and the accounting database answers mariadb, so a test meant to
read a fixture read the live cluster instead (and passed or failed by what the cluster
said). Tests run the code with

    sandbox_path(fakes, tools)

instead: FAKES (a directory of fake commands, or None) and a private directory holding
only TOOLS, the ordinary programs the code and its fakes use, linked from the host. A
command neither provides is not found, as on a host without it.
"""

import atexit
import shutil
import tempfile
from pathlib import Path


def sandbox_path(fakes=None, tools=()):
    """A PATH string of FAKES (if any) and a private directory holding only TOOLS."""
    holder = tempfile.TemporaryDirectory(prefix="sandbox-path-")
    atexit.register(holder.cleanup)
    for tool in tools:
        found = shutil.which(tool)
        if not found:
            raise RuntimeError(f"the test needs {tool}, which this host does not have")
        Path(holder.name, tool).symlink_to(found)
    return ":".join(([str(fakes)] if fakes else []) + [holder.name])
