"""Install the site's AI agent rules into each agent's system-wide admin channel.

Fetches one Markdown file (the site docs publish it) and writes it, unchanged, as
  - Claude Code managed memory (a CLAUDE.md every session loads), and
  - Codex requirements.toml `additional_developer_instructions`.
Users' own files are never touched. A failed download keeps what is installed; a
download that is not the rules file (marker missing, too large) is refused.
Exit status: 0 installed or kept, 1 nothing usable installed. Standard library only.
"""

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

MARKER = "<!-- cluster-agent-rules -->"
# Codex rejects developer instructions over ~10k tokens; stay well under it.
MAX_BYTES = 24_000
HEADER = "# Managed by ai-agent-rules; replaced on every refresh. Edit the site docs instead.\n"


def fetch(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"rules are larger than {MAX_BYTES} bytes")
    text = data.decode("utf-8")
    if not text.startswith(MARKER):
        raise ValueError("not an agent rules file (marker missing)")
    return text


def codex_requirements(text):
    # A JSON string is a valid TOML basic string (same escapes), so this is exact.
    return f"{HEADER}additional_developer_instructions = {json.dumps(text, ensure_ascii=False)}\n"


def write_atomic(path, content):
    """Replace path only if the content changed; returns True when it did."""
    path = Path(path)
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True)
    ap.add_argument("--claude", required=True, help="Claude Code managed CLAUDE.md path")
    ap.add_argument("--codex", required=True, help="Codex requirements.toml path")
    args = ap.parse_args(argv)
    targets = (Path(args.claude), Path(args.codex))
    try:
        text = fetch(args.url)
    except (OSError, ValueError, UnicodeDecodeError) as err:
        if all(t.is_file() for t in targets):
            print(f"kept the installed rules: {err}", file=sys.stderr)
            return 0
        print(f"no rules installed: {err}", file=sys.stderr)
        return 1
    changed = [
        str(t) for t, content in zip(targets, (text, codex_requirements(text))) if write_atomic(t, content)
    ]
    print(("installed " + ", ".join(changed)) if changed else "rules unchanged")
    return 0
