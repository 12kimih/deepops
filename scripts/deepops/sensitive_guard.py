#!/usr/bin/env python3
"""Keep secrets and site data out of git history.

Two rule sets:
  built-in  generic secret formats (private keys, tokens, webhooks). Public by nature.
  site      one site's private facts (hostnames, addresses, ports, hardware and account
            names). The list lives outside this public repository, by default in
            config/sensitive-patterns, so the list itself never leaks.

Site file, one rule per line, '#' starts a comment line:
  literal:TEXT==>REPLACEMENT
  regex:PATTERN==>REPLACEMENT        Python re; use (?i) for case-insensitive
  keys:VARS_FILE:VARIABLE==>REPL     every key of a YAML mapping VARIABLE in
                                     VARS_FILE (relative to the site file), as words
  allow:WORD                         drop WORD from keys rules (a key that is also
                                     an ordinary word, e.g. a shared account)
The same file feeds `replace-text`, the git-filter-repo input that scrubs history, so
detection and cleanup share one source.

Modes:
  staged            lines added in the index (pre-commit)
  message FILE      a commit message (commit-msg), every line above git's scissors
  push              commits a push would publish (pre-push): under pre-commit, which
                    passes on one ref only, everything not yet on that remote
  history REV...    lines added and messages of commits, e.g. main ^upstream/master
  tree              every tracked file in the work tree
  replace-text      print git-filter-repo --replace-text rules for the site file

Every mode checks file names too, renamed ones included, and a binary file's printable
runs. Findings name the place and the rule, never the matched text. A line containing
"sensitive-guard: allow" is skipped. Exit status: 0 clean, 1 findings, 2 usage error.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ALLOW = "sensitive-guard: allow"
DEFAULT_SITE_FILE = "config/sensitive-patterns"
ZERO_SHA = re.compile(r"^0+$")
SCISSORS = re.compile(r"^\S -{24} >8 -{24}$")  # git commit -v: the diff below is not the message
PRINTABLE_RUN = re.compile(rb"[\x20-\x7e]{6,}")  # what `strings` would show of a binary
# Names as they are, not quoted; renames as a deletion and an addition, so the new
# name and the whole content are seen; and the raw records, which name every file,
# a binary one too.
DIFF = ("-c", "core.quotePath=false")
DIFF_OPTS = ("--raw", "-p", "-U0", "--no-renames", "--no-color", "--no-ext-diff", "--no-textconv")


@dataclass(frozen=True)
class Rule:
    name: str
    regex: re.Pattern[str]
    replacement: str | None = None
    literal: str | None = None


BUILTIN = [
    Rule("private key", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")),
    Rule("Slack token", re.compile(r"\bxox[abposr]-[0-9A-Za-z-]{10,}")),
    Rule("Slack webhook", re.compile(r"hooks\.slack\.com/services/T[0-9A-Z]+/B[0-9A-Z]+/[0-9A-Za-z]{20,}")),
    Rule("GitHub token", re.compile(r"\b(?:gh[pousr]_[0-9A-Za-z]{36,}|github_pat_[0-9A-Za-z_]{40,})")),
    Rule("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    Rule(
        "Healthchecks ping URL",
        re.compile(r"hc-ping\.com/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
    ),
]


def git(*args: str, input_text: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], input=input_text, capture_output=True, text=True, errors="replace", check=True
    ).stdout


def blob(sha: str) -> bytes:
    return subprocess.run(["git", "cat-file", "blob", sha], capture_output=True, check=True).stdout


def mapping_keys(path: Path, variable: str) -> list[str]:
    """Direct keys of a top-level block mapping `variable:`; nested mappings are skipped."""
    keys, inside, indent = [], False, None
    for line in path.read_text().splitlines():
        if not inside:
            inside = re.fullmatch(rf"{re.escape(variable)}:\s*", line) is not None
            continue
        if line.strip() == "" or line.lstrip().startswith("#"):
            continue
        if not line[:1].isspace():
            break
        depth = len(line) - len(line.lstrip())
        indent = depth if indent is None else indent
        if depth != indent:
            continue
        match = re.match(r"\s+([^\s:#'\"][^:]*?|'[^']+'|\"[^\"]+\"):(?:\s|$)", line)
        if match:
            keys.append(match.group(1).strip("'\""))
    if not keys:
        raise ValueError(f"{path}: no keys under {variable}")
    return keys


def load_site_rules(path: Path) -> list[Rule]:
    lines = [(number, raw.strip()) for number, raw in enumerate(path.read_text().splitlines(), 1)]
    lines = [(number, line) for number, line in lines if line and not line.startswith("#")]
    allowed = {line[len("allow:") :] for _, line in lines if line.startswith("allow:")}
    rules = []
    for number, line in lines:
        if line.startswith("allow:"):
            continue
        pattern, sep, replacement = line.partition("==>")
        if not sep:
            raise ValueError(f"{path}:{number}: missing '==>'")
        kind, _, body = pattern.partition(":")
        name = f"site rule {number}"
        if kind == "literal":
            rules.append(Rule(name, re.compile(re.escape(body)), replacement, literal=body))
        elif kind == "regex":
            rules.append(Rule(name, re.compile(body), replacement))
        elif kind == "keys":
            vars_file, _, variable = body.rpartition(":")
            words = sorted(
                set(mapping_keys(path.parent / vars_file, variable)) - allowed, key=len, reverse=True
            )
            alternation = "|".join(re.escape(word) for word in words)
            rules.append(Rule(name, re.compile(rf"(?<![\w.-])(?:{alternation})(?![\w-])"), replacement))
        else:
            raise ValueError(f"{path}:{number}: unknown rule type {kind!r}")
    return rules


def site_file(root: Path, explicit: str | None) -> Path:
    configured = explicit or os.environ.get("DEEPOPS_SENSITIVE_PATTERNS")
    if not configured:
        configured = subprocess.run(
            ["git", "config", "--get", "deepops.sensitivePatterns"], capture_output=True, text=True
        ).stdout.strip()
    return Path(configured) if configured else root / DEFAULT_SITE_FILE


class Scanner:
    def __init__(self, rules: list[Rule]):
        self.rules = rules
        self.findings = 0

    def line(self, where: str, text: str) -> None:
        if ALLOW in text:
            return
        for rule in self.rules:
            if rule.regex.search(text):
                hint = f" (use {rule.replacement!r})" if rule.replacement else ""
                print(f"{where}: {rule.name}{hint}")
                self.findings += 1

    def text(self, where: str, text: str) -> None:
        for number, line in enumerate(text.splitlines(), 1):
            self.line(f"{where}:{number}", line)

    def binary(self, where: str, data: bytes) -> None:
        for run in PRINTABLE_RUN.findall(data):
            self.line(f"{where} (binary)", run.decode("ascii"))

    def diff(self, diff: str, prefix: str = "") -> None:
        """Output of `git diff`/`git log` with DIFF_OPTS: every file's name (raw records),
        the added lines, and a binary file's content."""
        blobs, path, number, header = {}, "?", 0, False
        for line in diff.splitlines():
            if line.startswith(":") and "\t" in line and not header:
                meta, _, name = line.partition("\t")  # ":mode mode sha sha STATUS\tname"
                fields = meta.split()
                if len(fields) == 5 and fields[4] != "D":
                    blobs[name] = fields[3]
                    self.line(f"{prefix}{name} (file name)", name)
            elif line.startswith("diff --git a/"):
                rest = line[len("diff --git a/") :]  # "NAME b/NAME": no renames, so one name
                path, number, header = rest[: (len(rest) - 3) // 2], 0, True
            elif header and line.startswith("Binary files "):
                if blobs.get(path) and not ZERO_SHA.match(blobs[path]):
                    self.binary(f"{prefix}{path}", blob(blobs[path]))
            elif line.startswith("@@"):
                match = re.search(r"\+(\d+)", line)
                number, header = (int(match.group(1)) if match else 0), False
            elif header:
                continue  # index, mode, ---/+++ lines: names are in the raw records
            elif line.startswith("+"):
                self.line(f"{prefix}{path}:{number}", line[1:])
                number += 1

    def commits(self, revs: list[str]) -> None:
        log = git(
            *DIFF, "log", "--format=%x01%H%n%B%x02", *DIFF_OPTS, "--diff-merges=first-parent", *revs, "--"
        )
        for chunk in log.split("\x01")[1:]:
            sha, _, rest = chunk.partition("\n")
            message, _, diff = rest.partition("\x02")
            self.text(f"commit {sha[:12]} message", message)
            self.diff(diff, prefix=f"commit {sha[:12]} ")


def push_revs() -> list[list[str]] | None:
    """Revision sets for a pre-push hook; None when git said nothing at all.

    pre-commit passes on only the first ref of a push (PRE_COMMIT_FROM_REF/TO_REF), none
    for a push that reaches a root commit, and keeps the hook's stdin. So under
    pre-commit every local branch and tag is checked against what the remote already
    has: a superset of the push, never less. Run as git's own hook, the refs come on
    stdin."""
    remote = os.environ.get("PRE_COMMIT_REMOTE_NAME")
    if remote:
        return [["--branches", "--tags", "--not", f"--remotes={remote}"]]
    lines = sys.stdin.read().splitlines()
    if not lines:
        return None
    sets = []
    for line in lines:
        _, local_sha, _, remote_sha = line.split()
        if ZERO_SHA.match(local_sha):
            continue
        sets.append(
            [local_sha, "--not", remote_sha]
            if not ZERO_SHA.match(remote_sha)
            else [local_sha, "--not", "--remotes"]
        )
    return sets


def replace_text(rules: list[Rule]) -> str:
    out = []
    for rule in rules:
        if rule.replacement is None:
            continue
        body = rule.literal if rule.literal is not None else f"regex:{rule.regex.pattern}"
        out.append(f"{body}==>{rule.replacement}")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--patterns",
        help=f"site file (default: $DEEPOPS_SENSITIVE_PATTERNS, "
        f"git config deepops.sensitivePatterns, then {DEFAULT_SITE_FILE})",
    )
    parser.add_argument("--no-site", action="store_true", help="built-in rules only (for site repositories)")
    parser.add_argument("mode", choices=["staged", "message", "push", "history", "tree", "replace-text"])
    parser.add_argument("args", nargs="*")
    opts = parser.parse_args()

    root = Path(git("rev-parse", "--show-toplevel").strip())
    os.chdir(root)
    rules = list(BUILTIN)
    if not opts.no_site:
        path = site_file(root, opts.patterns)
        if path.is_file():
            try:
                rules += load_site_rules(path)
            except (ValueError, OSError, re.error) as err:
                print(f"sensitive-guard: {err}", file=sys.stderr)
                return 2
        elif opts.mode == "replace-text" or opts.patterns:
            print(f"site patterns not found: {path}", file=sys.stderr)
            return 2
        else:
            print(f"sensitive-guard: no site patterns at {path}; built-in rules only", file=sys.stderr)
    if opts.mode == "replace-text":
        sys.stdout.write(replace_text(rules))
        return 0

    scan = Scanner(rules)
    if opts.mode == "staged":
        scan.diff(git(*DIFF, "diff", "--cached", *DIFF_OPTS, "--diff-filter=d"))
    elif opts.mode == "message":
        if len(opts.args) != 1:
            parser.error("message takes the message file")
        # Every line above the scissors: a "#" line stays in the commit when the message
        # came from -m or -F, and which lines git strips depends on settings, so none is
        # assumed comment. Below the scissors is the diff `commit -v` shows, not message.
        kept = []
        for line in Path(opts.args[0]).read_text(errors="replace").splitlines():
            if SCISSORS.match(line):
                break
            kept.append(line)
        scan.text("commit message", "\n".join(kept))
    elif opts.mode == "push":
        sets = push_revs()
        if sets is None:
            print("sensitive-guard: push: no refs to check were given", file=sys.stderr)
            return 2
        for revs in sets:
            scan.commits(revs)
    elif opts.mode == "history":
        if not opts.args:
            parser.error("history takes revisions, e.g. main ^upstream/master")
        scan.commits(opts.args)
    elif opts.mode == "tree":
        for name in git("ls-files", "-z").split("\0"):
            path = Path(name)
            if not name or not path.is_file() or path.is_symlink():
                continue
            data = path.read_bytes()
            scan.line(f"{name} (file name)", name)
            if b"\0" in data[:8192]:
                scan.binary(name, data)
            else:
                scan.text(name, data.decode(errors="replace"))

    if scan.findings:
        print(
            f"sensitive-guard: {scan.findings} finding(s). Replace them with generic values, "
            f"or mark a deliberate line with '{ALLOW}'.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
