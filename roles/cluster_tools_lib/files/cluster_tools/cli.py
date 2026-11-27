"""Command lines for the cluster's commands, from one definition per command.

A command lists its options once. From that list come the parser, the brief help
(-h), the full help (--help) and the bash completion (--completion bash), so the four
can never disagree. One vocabulary across the commands: an option two commands share
means the same in both (opt_me, opt_all, opt_user, opt_json below; tests/cluster-tools
checks every shared flag), and a renamed option keeps working, saying its new name. Every command also takes the same style options, and reads
defaults from an environment variable (SQ_OPTIONS for sq) as if typed first, so the
command line always has the last word. Python 3.9 or newer, standard library only.
"""

import argparse
import os
import pwd
import shlex
import sys

from . import render
from .exits import OK, UNKNOWN, CannotRun, NoAnswer, note

# Value completers: a single field per line from a standard Slurm command, so the
# completion never parses free text and never needs this package to be importable. They
# run at every <TAB>, so each is bounded: a slow or rate-limiting slurmctld must cost a
# missing suggestion, never a frozen shell.
_TAB_BOUND = "timeout 3 "
COMPLETERS = {
    "partitions": _TAB_BOUND + "sinfo -h -o %R 2>/dev/null",
    "nodes": _TAB_BOUND + "sinfo -h -N -o %N 2>/dev/null",
    "users": _TAB_BOUND + "squeue -h -o %u 2>/dev/null | sort -u",
    "flavours": "printf '%s\\n' " + " ".join(render.FLAVOURS),
    "color": "printf '%s\\n' auto always never",
}


class Opt:
    """One option. FLAGS as in argparse; BRIEF puts it in -h; NEGATE adds a --no- form;
    COMPLETE names a COMPLETERS entry for its value; SECTION groups it under --help. A
    single flag without a dash is a positional argument named METAVAR (required).
    ALIASES are accepted and never shown; RENAMED too, and a use of one says the new
    name (an option renamed for the shared vocabulary keeps working a while)."""

    def __init__(
        self,
        *flags,
        help,
        dest=None,
        metavar=None,
        default=None,
        negate=False,
        brief=False,
        complete=None,
        section="Options",
        action=None,
        const=None,
        type=None,
        choices=None,
        aliases=(),
        renamed=(),
        required=True,
    ):
        self.flags, self.help, self.dest, self.metavar = flags, help, dest, metavar
        self.required = required  # a positional only: false leaves it out when not given
        self.aliases, self.renamed = tuple(aliases), tuple(renamed)
        self.default, self.negate, self.brief = default, negate, brief
        self.complete, self.section = complete, section
        self.action, self.const, self.type, self.choices = action, const, type, choices

    @property
    def takes_value(self):
        return self.metavar is not None

    @property
    def positional(self):
        return not self.flags[0].startswith("-")

    def add_to(self, parser):
        kw = {"help": self.help}
        if self.positional:
            # Optional to argparse, so -h, --help and --completion work without it;
            # Command.parse requires it after those.
            parser.add_argument(
                self.flags[0], metavar=self.metavar, help=self.help, type=self.type, nargs="?"
            )
            return
        if self.dest:
            kw["dest"] = self.dest
        if self.negate:
            kw.update(action=argparse.BooleanOptionalAction, default=bool(self.default))
        elif self.takes_value:
            kw.update(metavar=self.metavar, default=self.default)
            if self.type:
                kw["type"] = self.type
            if self.choices:
                kw["choices"] = self.choices
        else:
            kw["action"] = self.action or "store_true"
            if self.const is not None:
                kw["const"] = self.const
            if self.default is not None:
                kw["default"] = self.default
        parser.add_argument(*self.flags, *self.aliases, *self.renamed, **kw)

    def label(self):
        if self.positional:
            return self.metavar if self.required else f"[{self.metavar}]"
        flags = ", ".join(self.flags)
        if self.negate:
            long = next(f for f in self.flags if f.startswith("--"))
            flags += f", --no-{long[2:]}"
        return f"{flags} {self.metavar}" if self.takes_value else flags


STYLE = [
    Opt(
        "--theme",
        metavar="FLAVOUR",
        complete="flavours",
        section="Style",
        help="catppuccin flavour: mocha (default), macchiato, frappe, latte. "
        "$CLUSTER_THEME sets it for every cluster command",
    ),
    Opt("--themes", section="Style", help="list the flavours with a swatch"),
    Opt(
        "--color",
        "--colour",
        metavar="WHEN",
        default="auto",
        complete="color",
        section="Style",
        choices=("auto", "always", "never", "yes", "no", "force", "off"),
        help="always, never or auto (default; $NO_COLOR is honoured)",
    ),
    Opt(
        "--no-color",
        "--no-colour",
        dest="color",
        action="store_const",
        const="never",
        section="Style",
        help="the same as --color never",
    ),
    Opt("--ascii", section="Style", help="ASCII glyphs for terminals without UTF-8"),
    Opt("--wide", section="Style", help="every column at full width, whatever the terminal fits"),
]


# The shared vocabulary: an option more than one command takes is made here, so its
# flags and meaning are the same in each (each command words its own help).


def opt_me(help, **kw):
    """-m/--me: only what is yours (squeue's own --me)."""
    return Opt("-m", "--me", help=help, brief=True, **kw)


def opt_all(help, **kw):
    """-a/--all: everyone's or everything, instead of the default subset (as squeue -a,
    docker ps -a)."""
    return Opt("-a", "--all", dest="all", help=help, brief=True, **kw)


def opt_user(help, metavar="USER", **kw):
    """-u/--user: the user or users to look at."""
    return Opt("-u", "--user", metavar=metavar, complete="users", help=help, brief=True, **kw)


def opt_json(help="the model as JSON, for scripts", **kw):
    """--json: the figures for scripts; it ignores the command's defaults variable."""
    return Opt("--json", section="Output", help=help, **kw)


class Command:
    """NAME and SUMMARY head the help (SUMMARY_KO is the same line in Korean, for the
    user docs and the Slack channel guides); OPTIONS are Opt; ENV names the defaults variable;
    MACHINE is the option whose output scripts read, which ignores ENV defaults; NOTES
    are extra paragraphs for --help, as text or as a function returning text; THEME_ENV
    pins a flavour for this command only, after $CLUSTER_THEME; REFUSE maps options this
    command deliberately does not take (another tool's) to the reason and what to use;
    JUDGES marks a command whose exit status 1 means at risk (cluster_tools.exits)."""

    def __init__(
        self,
        name,
        summary,
        options,
        env=None,
        machine=None,
        notes=(),
        usage=None,
        theme_env=None,
        refuse=None,
        summary_ko="",
        judges=False,
    ):
        self.judges = judges
        self.name, self.summary, self.options = name, summary, list(options) + STYLE
        self.summary_ko = summary_ko
        self.env, self.machine, self.notes = env, machine, list(notes)
        self.theme_env = theme_env
        self.refuse = dict(refuse or {})
        self.usage = usage or f"{name} [OPTIONS]"

    def refused(self, argv):
        """The first refused option in ARGV and why, or None. A long option matches with
        or without =VALUE, a short one also with its value attached (-o%i)."""
        for word in argv:
            if word == "--":
                break
            for flag, why in self.refuse.items():
                if (
                    word == flag
                    or word.startswith(flag + "=")
                    or (len(flag) == 2 and not word.startswith("--") and word.startswith(flag))
                ):
                    return flag, why
        return None

    def renames(self, argv):
        """(old flag, its new name) for each renamed option ARGV uses, once each."""
        seen = []
        for word in argv:
            if word == "--":
                break
            for o in self.options:
                for old in o.renamed:
                    if (word == old or word.startswith(old + "=")) and old not in (s[0] for s in seen):
                        seen.append((old, " / ".join(o.flags)))
        return seen

    def parser(self):
        p = argparse.ArgumentParser(prog=self.name, add_help=False, allow_abbrev=False)
        for o in self.options:  # --color before --no-color: its default wins
            o.add_to(p)
        p.add_argument("-h", action="store_true", dest="_brief")
        p.add_argument("--help", action="store_true", dest="_full")
        p.add_argument("--completion", metavar="SHELL", choices=("bash",), dest="_completion")
        return p

    def help(self, full):
        lines = [f"{self.name}: {self.summary}", "", f"Usage: {self.usage}"]
        shown = [o for o in self.options if full or o.brief]
        width = max(len(o.label()) for o in shown) + 2
        sections = []
        for o in shown:
            if o.section not in sections:
                sections.append(o.section)
        for s in sections:
            if full:
                lines += ["", f"{s}:"]
            for o in (x for x in shown if x.section == s):
                lines.append(f"  {o.label():<{width}}{o.help}")
        if full:
            for n in self.notes:
                text = n() if callable(n) else n
                lines += ["", text.rstrip()]
        lines += ["", note(self.judges)]
        if not full:
            lines += ["", f"More: {self.name} --help"]
        return "\n".join(lines) + "\n"

    def completion_parts(self):
        """(words, case lines) of this command's completion: its flags, and for each one
        that takes a value, what completes the value."""
        flagged = [o for o in self.options if not o.positional]
        words = sorted(
            {f for o in flagged for f in o.flags}
            | {f"--no-{f[2:]}" for o in flagged if o.negate for f in o.flags if f.startswith("--")}
            | {"-h", "--help"}
        )
        cases = []
        for o in flagged:
            if o.takes_value:
                # A named completer, else the option's own choices: a fixed set needs no command.
                src = COMPLETERS.get(o.complete) or (o.choices and "printf '%s\\n' " + " ".join(o.choices))
                pattern = "|".join(o.flags)
                body = f'COMPREPLY=($(compgen -W "$({src})" -- "$cur"))' if src else "COMPREPLY=()"
                cases.append(f"        {pattern}) {body}; return ;;")
        return words, cases

    def completion(self):
        """A bash completion function for this command, generated from its options."""
        words, cases = self.completion_parts()
        fn = "_" + self.name.replace("-", "_")
        return "\n".join(
            [
                f"# bash completion for {self.name}, generated by `{self.name} --completion bash`",
                f"{fn}() {{",
                "    local cur=${COMP_WORDS[COMP_CWORD]} prev=${COMP_WORDS[COMP_CWORD-1]}",
                '    case "$prev" in',
                *cases,
                "    esac",
                f'    COMPREPLY=($(compgen -W "{" ".join(words)}" -- "$cur"))',
                "}",
                f"complete -F {fn} {self.name}",
                "",
            ]
        )

    def parse(self, argv=None, env=None):
        """Parsed options and a Theme, after the help, completion and theme-list exits."""
        env = os.environ if env is None else env
        argv = list(sys.argv[1:] if argv is None else argv)
        if self.env and env.get(self.env) and not (self.machine and self.machine in argv):
            try:
                argv = shlex.split(env[self.env]) + argv
            except ValueError as e:  # an unbalanced quote
                self.parser().error(f"${self.env}: {e}")
        refused = self.refused(argv)
        if refused:
            self.parser().error(f"{refused[0]}: {refused[1]}")
        for old, new in self.renames(argv):
            print(f"{self.name}: {old} is now {new}", file=sys.stderr)
        args = self.parser().parse_args(argv)
        if args._brief or args._full:
            sys.stdout.write(self.help(full=args._full))
            raise SystemExit(0)
        if args._completion:
            sys.stdout.write(self.completion())
            raise SystemExit(0)
        missing = [
            o.metavar
            for o in self.options
            if o.positional and o.required and getattr(args, o.flags[0]) in (None, "")
        ]
        if missing:
            self.parser().error(f"{' '.join(missing)} is required (usage: {self.usage})")
        if self.machine and getattr(args, self.machine.lstrip("-").replace("-", "_"), False):
            args.color = "never"
        flavour = args.theme or env.get("CLUSTER_THEME") or (self.theme_env and env.get(self.theme_env))
        if flavour and not render.flavour_name(flavour):
            print(
                f"{self.name}: unknown theme {flavour!r}, using {render.DEFAULT_FLAVOUR} (try --themes)",
                file=sys.stderr,
            )
        theme = render.detect(render.flavour_name(flavour), args.color, args.ascii, args.wide, env=env)
        if args.themes:
            sys.stdout.write(swatch(theme))
            raise SystemExit(0)
        return args, theme


def swatch(theme):
    """Every flavour with a strip of its colours, the active one marked."""
    out = []
    for f in render.FLAVOURS:
        t = render.Theme(f, theme.color, theme.depth, theme.ascii, theme.cols)
        strip = "".join(
            t.paint(r, t.g["bar_f"])
            for r in ("ok", "warn", "hot", "crit", "info", "part", "self", "acc", "text", "muted")
        )
        mark = "  " + t.paint("sub", "(active)") if f == theme.flavour else ""
        out.append(f"  {t.paint(t.c['acc'] + t.bold, f'{f:<11}')}{strip}{mark}")
    out += [
        "",
        "  "
        + theme.paint("sub", "Set it for every cluster command with:")
        + f" export CLUSTER_THEME={theme.flavour}",
    ]
    return "\n".join(out) + "\n"


def line_output(stream=None):
    """Write STREAM (stdout) a line at a time, as a terminal gets it, wherever it goes.
    Under systemd it is a journald stream, which Python buffers 8 KiB at a time: a
    long-running program's lines (cluster-bot's "dm ...") showed up late or never, and a
    stop (SIGTERM ends Python without flushing) or a timeout kill dropped them. Every
    program of ours calls this first: the cluster command launcher and the cluster_bot
    package. PYTHONUNBUFFERED cannot do it: the launcher runs python -I, which ignores
    it. stderr is line-buffered already."""
    stream = sys.stdout if stream is None else stream
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(line_buffering=True)


def me():
    """Who runs the command: $USER, as the login set it, else the account of the uid;
    the uid itself when even that cannot be looked up (the directory service is down)."""
    if os.environ.get("USER"):
        return os.environ["USER"]
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return str(os.getuid())


def run_main(name, body):
    """Run BODY() -> exit status, with the failures every command shares, each as one
    line and with its status (cluster_tools.exits): CannotRun (a config's, a missing
    Slurm client, a request refused) 2, NoAnswer (Slurm's, Prometheus's) and a file's
    OSError 3. Ctrl-C and a closed pipe end quietly. Anything else is a bug and keeps
    its traceback, rather than being reported as a source's."""
    try:
        return body() or OK
    except (CannotRun, NoAnswer) as e:  # a config's names the file and the key
        print(f"{name}: {e}", file=sys.stderr)
        return e.status
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        sys.stderr.close()  # `qos | head`: nothing left to say
        return OK
    except OSError as e:
        print(f"{name}: {e.filename}: {e.strerror}" if e.filename else f"{name}: {e}", file=sys.stderr)
        return UNKNOWN


class Group:
    """A command of subcommands, NAME [SUB] [OPTIONS]: without one, DEFAULT runs (a
    module); with one, the module SUBS names for it. Each module has COMMAND (named
    "NAME SUB" for a subcommand) and main(argv), and keeps its own domain's code: this
    only dispatches, and makes the one bash completion of them all."""

    def __init__(self, name, default, subs):
        self.name, self.default, self.subs = name, default, dict(subs)

    def module(self, sub=None):
        import importlib

        return importlib.import_module(self.subs[sub] if sub else self.default)

    def commands(self):
        """[(sub, COMMAND)] in order; the default first, with sub None."""
        return [(None, self.module().COMMAND)] + [(s, self.module(s).COMMAND) for s in self.subs]

    def main(self, argv=None):
        argv = list(sys.argv[1:] if argv is None else argv)
        if argv and argv[0] in self.subs:
            return self.module(argv[0]).main(argv[1:])
        if argv[:2] == ["--completion", "bash"]:
            sys.stdout.write(self.completion())
            return OK
        return self.module().main(argv)

    def completion(self):
        """One bash completion function: the subcommands and the default's flags at the
        first word, then the subcommand's own flags and values."""
        fn = "_" + self.name.replace("-", "_")
        branches = []
        # The subcommands first: the default's branch (*) catches every other word.
        for sub, command in self.commands()[1:] + self.commands()[:1]:
            words, cases = command.completion_parts()
            label = sub or "*"
            branches += [
                f"        {label})",
                '            case "$prev" in',
                *("    " + c for c in cases),
                "            esac",
                f'            words="{" ".join(words)}" ;;',
            ]
        first = " ".join(list(self.subs) + self.commands()[0][1].completion_parts()[0])
        return "\n".join(
            [
                f"# bash completion for {self.name}, generated by `{self.name} --completion bash`",
                f"{fn}() {{",
                "    local cur=${COMP_WORDS[COMP_CWORD]} prev=${COMP_WORDS[COMP_CWORD-1]} words",
                '    case "${COMP_WORDS[1]}" in',
                *branches,
                "    esac",
                f'    [ "$COMP_CWORD" -eq 1 ] && words="{first}"',
                '    COMPREPLY=($(compgen -W "$words" -- "$cur"))',
                "}",
                f"complete -F {fn} {self.name}",
                "",
            ]
        )
