"""roles/nhc/files/deepops.nhc's checks run as NHC runs them, for the tests.

    subprocess.run(nhc_checks.argv("check_nv_gpu_count 8"), ...)

A failing check calls NHC's die with its reason; here die prints that reason and ends
the check with its status, which is what a check (and Slurm's drain reason) sees.
"""

from pathlib import Path

CHECKS = Path(__file__).resolve().parents[2] / "roles" / "nhc" / "files" / "deepops.nhc"

DIE = 'die() { local rc="$1"; shift; echo "$*"; exit "$rc"; }'


def argv(call):
    """bash running CALL (a check and its arguments, shell words) after the checks load."""
    return ["bash", "-c", f"{DIE}; . {CHECKS}; {call}"]
