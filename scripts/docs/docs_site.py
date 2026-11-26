#!/usr/bin/env python3
"""The user docs site against what it is derived from: the code and the site config.

    docs_site.py [--docs DIR] [--ref REF] [--config DIR] check

The docs site is a repository of its own that copies facts from this one and the site
config: its data file, the agent rules the cluster pins, the commands it documents.
Each copy goes stale where the change is made (a command added here, a value changed in
the config) without anything there saying so: disk-usage shipped and stayed missing
from the docs (2026-11-27), because the one check ran only when an environment variable
nobody set named the docs checkout. This runs on every scripts/check.sh, against the
docs repository's REF (its local main, never its working tree: a branch someone has
checked out there must not change what this sees), and fails on any of:

  data      data/site.json is not what scripts/docs/site_facts.py writes now
  url       zensical.toml's site_url is not cluster_info.docs_url (where the login
            banner, the bot and the agent rules send people)
  agents    the AGENTS.md the docs' exporter (scripts/agent_rules.py) writes at REF does
            not hash to ai_agent_rules_sha256, the copy the cluster installs
  commands  a published command (cluster_tools_commands) without an options table
            (options:<name>, or options:all for every command of the data file), not on a
            commands table (commands:table, or named on a page), missing from the login
            banner (cluster_tools_motd_commands) or from the help canvas ([[commands]] in
            slack/help.md); a bot subcommand no page names (/cluster <name>, bot:<name>)

DIR is the docs checkout, by default cluster-docs beside the main checkout of this
repository (a worktree's too); none there (CI), nothing to compare.
"""

import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "deepops"))
import cli  # noqa: E402

spec = importlib.util.spec_from_file_location("site_facts", ROOT / "scripts/docs/site_facts.py")
site_facts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_facts)

PAGES = ("docs", "snippets")


def main_checkout():
    """The main checkout of this repository (a worktree's own)."""
    common = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True, errors="replace", check=True,
    ).stdout.strip()  # fmt: skip
    return Path(common).parent


def default_docs():
    return Path(os.environ.get("DEEPOPS_DOCS_SITE") or main_checkout().parent / "cluster-docs")


class Docs:
    """The docs repository at REF, read through git alone."""

    def __init__(self, path, ref):
        self.path, self.ref = Path(path), ref

    def git(self, *args, check=True):
        return subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            errors="replace",
            check=check,
        )

    def show(self, rel):
        done = self.git("show", f"{self.ref}:{rel}", check=False)
        return done.stdout if done.returncode == 0 else None

    def grep(self, *patterns):
        """The pages at REF holding any of PATTERNS (fixed strings)."""
        args = [a for p in patterns for a in ("-e", p)]
        done = self.git("grep", "-l", "-F", *args, self.ref, "--", *PAGES, check=False)
        return done.stdout.split()

    def export_agents(self):
        """The AGENTS.md the docs' exporter writes at REF, run in a shared clone of it."""
        with tempfile.TemporaryDirectory(prefix="docs-site-") as tmp:
            clone = Path(tmp) / "docs"
            subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", str(self.path), str(clone)],
                           check=True, capture_output=True)  # fmt: skip
            subprocess.run(["git", "-C", str(clone), "checkout", "-q", "--detach", self.git("rev-parse", self.ref).stdout.strip()],
                           check=True, capture_output=True)  # fmt: skip
            done = subprocess.run([sys.executable, "scripts/agent_rules.py", "--out", "-"], cwd=clone,
                                  capture_output=True, text=True, errors="replace")  # fmt: skip
            if done.returncode:
                raise RuntimeError(f"scripts/agent_rules.py at {self.ref}: {done.stderr.strip()[-300:]}")
            return done.stdout


def names(entries):
    return [e["name"] if isinstance(e, dict) else e for e in entries or []]


def problems(docs, config):
    """What is stale, one line each."""
    out = []
    s = site_facts.settings(config) if config else {}
    every = site_facts.facts(config)
    data = docs.show("data/site.json")
    want = site_facts.render(site_facts.public(every))
    if data != want:
        out.append(f"data: {docs.ref}:data/site.json is not what site_facts.py writes: run "
                   f"scripts/docs/site_facts.py --public {docs.path}/data/site.json and commit it there")  # fmt: skip
    toml = docs.show("zensical.toml")
    url = str((s.get("cluster_info") or {}).get("docs_url") or "")
    if toml is not None and url:
        site = str((tomllib.loads(toml).get("project") or {}).get("site_url") or "")
        if site.rstrip("/") != url.rstrip("/"):
            out.append(f"url: {docs.ref}:zensical.toml site_url {site} is not cluster_info.docs_url {url}")
    pinned = str(s.get("ai_agent_rules_sha256") or "")
    if pinned and docs.show("scripts/agent_rules.py") is not None:
        got = hashlib.sha256(docs.export_agents().encode()).hexdigest()
        if got != pinned:
            out.append(f"agents: the AGENTS.md {docs.ref} exports hashes to {got}, "
                       f"ai_agent_rules_sha256 pins {pinned}: pin it once the site publishes it")  # fmt: skip
    commands = names(s.get("cluster_tools_commands"))
    banner = names(s.get("cluster_tools_motd_commands", commands))
    canvas = docs.show("slack/help.md")
    # The data file holds every command (data, above), so a block that lists them all from
    # it (options:all, commands:table) covers each; else each needs its own.
    every_option = bool(docs.grep("BEGIN GENERATED: options:all "))
    table = bool(docs.grep("BEGIN GENERATED: commands:table "))
    for name in commands:
        if not every_option and not docs.grep(f"BEGIN GENERATED: options:{name} "):
            out.append(
                f"commands: {name} has no options table on a page at {docs.ref} (options:{name} or options:all)"
            )
        if not table and not docs.grep(f"`{name}`"):
            out.append(f"commands: no page at {docs.ref} names {name} (or lists them all: commands:table)")
        if name not in banner:
            out.append(f"commands: {name} is not in the login banner (cluster_tools_motd_commands)")
    if commands and canvas is not None and "[[commands]]" not in canvas:
        out.append(f"commands: {docs.ref}:slack/help.md does not list the commands ([[commands]])")
    command = str(s.get("cluster_bot_command") or "/cluster")
    for sub in every["bot"]["subcommands"]:
        if not docs.grep(f"{command} {sub['name']}", f"BEGIN GENERATED: bot:{sub['name']} "):
            out.append(f"commands: no page at {docs.ref} names {command} {sub['name']}")
    return out


def main(argv=None):
    ap = cli.parser(__doc__)
    ap.add_argument("--docs", type=Path, default=None)
    ap.add_argument("--ref", default="main")
    ap.add_argument("--config", default=os.environ.get("DEEPOPS_CONFIG_DIR", str(ROOT / "config")))
    ap.add_argument("action", choices=["check"])
    args = ap.parse_args(argv)
    path = args.docs or default_docs()
    if not (path / ".git").exists():
        if args.docs or os.environ.get("DEEPOPS_DOCS_SITE"):
            print(f"{path}: no docs checkout there", file=sys.stderr)
            return 1
        print(f"no docs checkout at {path}: nothing to compare")
        return 0
    docs = Docs(path, args.ref)
    if docs.git("rev-parse", "--verify", "-q", f"{args.ref}^{{commit}}", check=False).returncode:
        print(f"{path}: no {args.ref} there", file=sys.stderr)
        return 1
    config = args.config if Path(args.config).is_dir() else None
    found = problems(docs, config)
    for line in found:
        print(line, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
