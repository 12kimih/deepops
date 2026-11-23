#!/bin/sh
# git pre-push hook (installed by scripts/setup.sh): every push guard checks exactly the
# commits this push sends, from the refs git gives on stdin, whichever branch, tag or
# detached HEAD they come from. pre-commit hands its hooks only the first ref and keeps
# stdin, so these guards are not pre-commit hooks: under it a push from a detached HEAD
# went unchecked and a peer's unpushed branch blocked everyone else's push.
set -eu
refs="$(cat)"
[ -n "${refs}" ] || exit 0
guards="$(dirname "$(readlink -f "$0")")"
status=0
for guard in sensitive_guard.py no_silent_revert.py commit_message.py ; do
    printf '%s\n' "${refs}" | "${guards}/${guard}" push || status=1
done
exit "${status}"
