"""scratch_space: the fast scratch space's cleanup, and what each person sees of it.

    scratch-space                       (people) what of yours is due to go, and when
    scratch-space CONFIG.json purge [--check]   (root, a daily timer) find, list, remove

The scratch space is for data in use. A file nobody has used (read or written: the later of its
access and modification times) for stale_days is due: it is listed in its owner's list
file and removed grace_days after it was first listed, unless it is used before then.

A file read over NFS from a client's page cache is not seen as used: the client checks
its attributes and reads its cached copy, so the server's access time stays. A job that
keeps re-reading the same small files for weeks could have them taken mid-run. So a file
whose date has come is not removed while its owner has a running or suspended Slurm
job: it is deferred once, by stale_days more, marked so in the list and the metrics, and
goes on that date whatever runs then (never a standing exemption). When Slurm cannot be
read, nothing is removed that run.
Using it is the only way to keep it: the next run finds it in use and drops it from
the list. The clock of a listed file is kept in the state file, so the grace period
starts when its owner was first told, never earlier.

Only each active person's own directory (<root>/<login>) and the collaboration folders
(<projects>/<name>/...) are looked at, every file counted to its owner. Nothing is
followed through a symbolic link and nothing on another filesystem is entered; a person's
own directory, a collaboration folder itself and the list files are never removed, and
a directory is removed only when this run emptied it. Names may hold any bytes.

The list file, <root>/<login>/<list_name>, is rewritten each run (gone when nothing is
due): one line per file, its removal date, its size and its path. `scratch-space` reads
it, and looks at each file again, so a file used since the list was written shows as
spared at once. The daily disk report (cluster_tools.usage_report --disk) tells each
person once per new removal date, from this run's textfile metrics:

  scratch_space_purge_due_files{user}               files listed
  scratch_space_purge_deferred_files{user}          of them, deferred once for running jobs
  scratch_space_purge_due_bytes{user}               their size
  scratch_space_purge_next_timestamp_seconds{user}  the earliest removal date
  scratch_space_purge_burst_files{user}             old files all used within one
                                                    burst_window: a mass access-time
                                                    refresh, for an administrator to see
  scratch_space_purge_removed_files, scratch_space_purge_removed_bytes   this run
  scratch_space_purge_scanned_files, scratch_space_purge_truncated
  scratch_space_purge_stale_days
  scratch_space_last_success_timestamp_seconds
  disk_cleanup_candidate_bytes, disk_cleanup_candidate_unused_days (the cleanup_top
  folders most worth cleaning, cluster_tools.cleanup, from the same walk)

Refreshing access times only to escape the cleanup is against the cluster's rules;
the burst figure makes it visible, and nothing is done about it automatically.
Python 3.9 or newer, standard library only.
"""

import errno
import grp
import json
import os
import pwd
import stat
import sys
import time
from collections import defaultdict

from . import cleanup, config, site, slurm, vocab
from .cleanup import last_use, walk
from .cli import Command, Opt, run_main
from .files import write_atomic, write_textfile
from .prom import key, render
from .render import size

DAY = 86400
LIST_NAME = ".purge-list"
HEADER = "# scratch-space purge list"

CONFIG_KEYS = {
    "root": str,
    "textfile_kept": str,
    "projects": str,
    "people": dict,
    "list_name": str,
    "stale_days": int,
    "grace_days": int,
    "max_entries": int,
    "burst_files": int,
    "burst_share": float,
    "burst_window": int,
    "cleanup_top": int,
    "state": str,
    "textfile": str,
}


def load_config(path):
    """The config roles/scratch_space writes (cluster_tools.config)."""
    cfg = config.require(path, CONFIG_KEYS)
    if cfg["stale_days"] < 1 or cfg["grace_days"] < 1:
        raise config.ConfigError(f"{path}: stale_days and grace_days are 1 or more")
    if not (cfg["root"].startswith("/") and cfg["projects"].startswith("/")):
        raise config.ConfigError(f"{path}: root and projects are absolute paths")
    return cfg


class Host:
    """The filesystem the tool reads and changes: replaced in the tests."""

    def lstat(self, path):
        return os.lstat(path)

    def running_users(self):
        """Logins with a job that holds its allocation (vocab.job_holds: running, still
        configuring, suspended, completing...), or None when Slurm cannot be read (the
        purge then removes nothing)."""
        try:
            jobs = slurm.load("jobs")["jobs"]
        except slurm.Error:
            return None
        return {j["user_name"] for j in jobs if vocab.job_holds(j["job_state"])}

    def folder_members(self, folder):
        """The logins of a collaboration folder's group (one group each), or None when it
        cannot be read."""
        try:
            return set(grp.getgrgid(os.lstat(folder).st_gid).gr_mem)
        except (OSError, KeyError):
            return None

    root = None  # set by scan: directories under it are read without following a link

    def scandir(self, path):
        """PATH's entries; under the root reached as open_parent does, so a directory
        swapped for a link while the walk runs lists nothing of another person's."""
        if not (self.root and path.startswith(self.root + os.sep)):
            return list(os.scandir(path))
        fd, name = self.open_parent(self.root, path)
        try:
            dfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        finally:
            os.close(fd)
        try:
            return list(os.scandir(dfd))
        finally:
            os.close(dfd)

    def open_parent(self, root, path):
        """(directory fd, name) of PATH under ROOT, reached one component at a time
        without following a link nor leaving ROOT's filesystem: a directory a user swaps
        for a link between the scan and the removal leads nowhere (OSError)."""
        parts = os.path.relpath(path, root).split(os.sep)
        if parts[0] in ("", ".", ".."):
            raise OSError(errno.EINVAL, "not under the root", path)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(root, flags)
        try:
            dev = os.fstat(fd).st_dev
            for part in parts[:-1]:
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
                if os.fstat(fd).st_dev != dev:
                    raise OSError(errno.EXDEV, "another filesystem", path)
        except BaseException:
            os.close(fd)
            raise
        return fd, parts[-1]

    def unlink_same(self, root, path, ident, cutoff):
        """Remove PATH only when it is still the file the scan saw (IDENT: device, inode,
        owner), a file or link unused since CUTOFF; whether it went."""
        fd, name = self.open_parent(root, path)
        try:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if (st.st_dev, st.st_ino, st.st_uid) != tuple(ident) or last_use(st) >= cutoff:
                return False
            if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
                return False
            os.unlink(name, dir_fd=fd)
            return True
        finally:
            os.close(fd)

    def rmdir(self, root, path):
        fd, name = self.open_parent(root, path)
        try:
            os.rmdir(name, dir_fd=fd)
        finally:
            os.close(fd)

    def write_list(self, directory, name, text, uid):
        """TEXT as DIRECTORY/NAME, owned by UID, readable by all, without following a link
        the directory's owner may have put there: a new file opened in the directory
        itself (no link followed to reach it), then renamed over the name."""
        dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for _ in range(10):
                tmp = f".{name}.{os.urandom(6).hex()}"
                try:
                    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dfd)
                    break
                except FileExistsError:
                    continue
            else:
                raise OSError(f"{directory}: no temporary name for {name}")
            try:
                with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as f:
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                    os.fchown(f.fileno(), uid, -1)
                    os.fchmod(f.fileno(), 0o644)
                os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            except BaseException:
                try:
                    os.unlink(tmp, dir_fd=dfd)
                except OSError:
                    pass
                raise
        finally:
            os.close(dfd)

    def remove_list(self, directory, name):
        dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.unlink(name, dir_fd=dfd)
        except FileNotFoundError:
            pass
        finally:
            os.close(dfd)


def escape(path):
    """PATH (str, any bytes as surrogates) on one line: backslash, tab and newline escaped."""
    return path.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


def unescape(text):
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            out.append({"\\": "\\", "t": "\t", "n": "\n"}.get(text[i + 1], text[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def projects_of(host, cfg):
    """The collaboration folders' names under the projects directory."""
    try:
        return sorted(e.name for e in host.scandir(cfg["projects"]))
    except OSError:
        return []


def tops(host, cfg):
    """[(directory, login or None, is_own)]: each active person's directory, and each
    collaboration folder (files there count to their owners)."""
    out = []
    for login in sorted(cfg["people"]):
        out.append((os.path.join(cfg["root"], login), login, True))
    try:
        for entry in sorted(host.scandir(cfg["projects"]), key=lambda e: e.name):
            out.append((os.path.join(cfg["projects"], entry.name), None, False))
    except OSError:
        pass
    return out


def scan(host, cfg, now):
    """What the filesystem holds: ({path: (login, size, last_use, (dev, ino, uid))} of the files with an
    owner in the roster, {login: files} of everyone's, {login: burst files}, entries
    looked at, whether the budget ran out)."""
    root_st = host.lstat(cfg["root"])
    dev = root_st.st_dev
    host.root = cfg["root"]
    login_of = {int(uid): login for login, uid in cfg["people"].items()}
    files, totals, bursts = {}, defaultdict(int), {}
    buckets = defaultdict(lambda: defaultdict(int))
    budget = [cfg["max_entries"]]
    stale = cfg["stale_days"] * DAY
    for top, login, own in tops(host, cfg):
        try:
            st = host.lstat(top)
        except OSError:
            continue
        if not stat.S_ISDIR(st.st_mode) or st.st_dev != dev:
            continue
        skip = {os.path.join(top, cfg["list_name"])} if own else set()
        for path, fst in walk(host, top, dev, skip, budget):
            owner = login_of.get(fst.st_uid, login if own else None)
            if owner is None:
                continue
            totals[owner] += 1
            files[path] = (owner, fst.st_size, last_use(fst), (fst.st_dev, fst.st_ino, fst.st_uid))
            # An old file (unchanged for stale_days) used in the last two days: the
            # bucket of its access time, for the burst figure.
            if fst.st_atime >= now - 2 * DAY and fst.st_atime - fst.st_mtime > stale:
                buckets[owner][int(fst.st_atime) // cfg["burst_window"]] += 1
    for owner, by in buckets.items():
        top_count = max(by.values())
        if top_count >= cfg["burst_files"] and top_count >= cfg["burst_share"] * totals[owner]:
            bursts[owner] = top_count
    return files, dict(totals), bursts, cfg["max_entries"] - budget[0], budget[0] <= 0


def load_state(path):
    try:
        with open(path) as f:
            state = json.load(f)
    except FileNotFoundError:
        return {"due": {}}
    except (OSError, ValueError) as e:
        raise RuntimeError(f"{path}: {e}") from None
    if not isinstance(state.get("due"), dict):
        raise RuntimeError(f"{path}: no due mapping")
    return state


def plan(cfg, files, state, truncated, now):
    """{path: {user, due, since}}: every stale file with its removal date, the date kept
    from STATE for a file listed before (its owner was told then), now plus the grace
    period for a new one. With TRUNCATED (the walk stopped early), files the walk did
    not reach keep their entries."""
    stale = now - cfg["stale_days"] * DAY
    before = state.get("due", {})
    due = {}
    for path, (owner, nbytes, used, ident) in files.items():
        if used >= stale:
            continue
        old = before.get(path)
        when = old["due"] if isinstance(old, dict) and "due" in old else now + cfg["grace_days"] * DAY
        since = old.get("since", now) if isinstance(old, dict) else now
        due[path] = {"user": owner, "due": when, "since": since, "bytes": nbytes, "ident": list(ident)}
        if isinstance(old, dict) and old.get("deferred"):
            due[path]["deferred"] = old["deferred"]  # once only: the date it was moved
    if truncated:
        for path, old in before.items():
            if path not in files and path not in due:
                due[path] = old
    return due


def still_stale(host, path, dev, cutoff):
    """Whether PATH is still a file or link on DEV unused since CUTOFF, just before it goes."""
    try:
        st = host.lstat(path)
    except OSError:
        return False
    kind_ok = stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)
    return kind_ok and st.st_dev == dev and last_use(st) < cutoff


def remove(host, cfg, due, now, check, say):
    """Remove each due file whose date has come (looked at again first), then the
    directories that removal emptied, deepest first; (files, bytes) removed. A file of a
    person with a running job is deferred once instead; nothing goes when Slurm is
    unreadable."""
    dev = host.lstat(cfg["root"]).st_dev
    cutoff = now - cfg["stale_days"] * DAY
    removed, nbytes, parents = 0, 0, set()
    ready = [p for p in sorted(due) if due[p]["due"] <= now]
    busy = host.running_users() if ready else set()
    if busy is None:
        say("Slurm could not be read: nothing is removed this run (a running job's files must not go)")
        return removed, nbytes
    members = {}

    def busy_with(path, owner):
        """Who keeps PATH busy: its owner, or in a collaboration folder any member (a
        member's job may read a colleague's files); unreadable members count as busy."""
        if owner in busy:
            return owner
        rel = os.path.relpath(path, cfg["projects"]).split(os.sep)
        if rel[0] in ("", ".", ".."):
            return None
        folder = os.path.join(cfg["projects"], rel[0])
        if folder not in members:
            members[folder] = host.folder_members(folder)
        if members[folder] is None:
            return f"a member of {rel[0]} (its group cannot be read)"
        holders = sorted(members[folder] & busy)
        return f"{holders[0]} (a member of {rel[0]})" if holders else None

    for path in ready:
        entry = due[path]
        if not still_stale(host, path, dev, cutoff):
            continue
        who = busy_with(path, entry["user"])
        if who and not entry.get("deferred"):
            entry["due"], entry["deferred"] = now + cfg["stale_days"] * DAY, now
            say(f"defer {escape(path)}: {who} has running jobs; it goes on its new date")
            continue
        if not entry.get("ident"):
            continue  # kept from a cut-short walk: removed only once a scan has seen it again
        say(f"remove {escape(path)}")
        if not check:
            try:
                gone = host.unlink_same(cfg["root"], path, entry["ident"], cutoff)
            except OSError as e:
                say(f"could not remove {escape(path)}: {e.strerror}")
                continue
            if not gone:
                say(f"keep {escape(path)}: it is no longer the file that was listed")
                continue
        removed += 1
        nbytes += entry.get("bytes", 0)
        parents.add(os.path.dirname(path))
        del due[path]
    if not check:
        keep = {os.path.join(cfg["root"], login) for login in cfg["people"]} | {cfg["root"], cfg["projects"]}
        for d in sorted(parents, key=len, reverse=True):
            # Up from each emptied directory to the person's own or the folder itself:
            # rmdir takes only an empty one, so a directory something else is in stays.
            while (
                d not in keep and os.path.dirname(d) != cfg["projects"] and d.startswith(cfg["root"] + os.sep)
            ):
                try:
                    host.rmdir(cfg["root"], d)
                except OSError:
                    break
                say(f"remove the emptied directory {escape(d)}")
                d = os.path.dirname(d)
    return removed, nbytes


def list_text(cfg, entries, now):
    """A person's list file: a header, then one line per due file, earliest first."""
    head = [
        f"{HEADER}, written {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}",
        f"# stale_days={cfg['stale_days']} grace_days={cfg['grace_days']}",
        "# Each file goes on its date unless it is used before (read or written); `scratch-space`",
        "# shows the list with what was used since. DATE<TAB>BYTES<TAB>PATH",
    ]
    held = sum(1 for _, e in entries if e.get("deferred"))
    if held:
        head.append(f"# {held} were deferred once because you had running jobs: they go on their date"
                    " even if jobs still run then, unless used.")  # fmt: skip
    lines = [
        f"{time.strftime('%Y-%m-%d', time.localtime(e['due']))}\t{e.get('bytes', 0)}\t{escape(p)}"
        for p, e in sorted(entries, key=lambda pe: (pe[1]["due"], pe[0]))
    ]
    return "\n".join(head + lines) + "\n"


def write_lists(host, cfg, due, now, say):
    """Each active person's list file, or none where nothing of theirs is due."""
    mine = defaultdict(list)
    for path, entry in due.items():
        mine[entry["user"]].append((path, entry))
    for login, uid in sorted(cfg["people"].items()):
        directory = os.path.join(cfg["root"], login)
        try:
            if not stat.S_ISDIR(host.lstat(directory).st_mode):
                continue
            if mine.get(login):
                host.write_list(directory, cfg["list_name"], list_text(cfg, mine[login], now), int(uid))
            else:
                host.remove_list(directory, cfg["list_name"])
        except OSError as e:
            say(f"could not write the list of {login}: {e}")


def metrics(cfg, due, bursts, removed, scanned, truncated, now, candidates=()):
    values = cleanup.values(cfg["root"], candidates, now) if candidates is not None else {}
    per = defaultdict(lambda: [0, 0, None, 0])
    for entry in due.values():
        p = per[entry["user"]]
        p[0] += 1
        p[1] += entry.get("bytes", 0)
        p[2] = entry["due"] if p[2] is None else min(p[2], entry["due"])
        p[3] += 1 if entry.get("deferred") else 0
    for user, (n, nbytes, nxt, held) in sorted(per.items()):
        values[key("scratch_space_purge_due_files", user=user)] = n
        values[key("scratch_space_purge_deferred_files", user=user)] = held
        values[key("scratch_space_purge_due_bytes", user=user)] = nbytes
        values[key("scratch_space_purge_next_timestamp_seconds", user=user)] = int(nxt)
    for user, n in sorted(bursts.items()):
        values[key("scratch_space_purge_burst_files", user=user)] = n
    values[key("scratch_space_purge_removed_files")] = removed[0]
    values[key("scratch_space_purge_removed_bytes")] = removed[1]
    values[key("scratch_space_purge_scanned_files")] = scanned
    values[key("scratch_space_purge_truncated")] = int(truncated)
    values[key("scratch_space_purge_stale_days")] = cfg["stale_days"]
    values[key("scratch_space_last_success_timestamp_seconds")] = int(now)
    return render(values)


def purge(host, cfg, now, check=False, say=print):
    """One daily run; with CHECK, says what it would remove and changes nothing."""
    state = load_state(cfg["state"])
    files, _, bursts, scanned, truncated = scan(host, cfg, now)
    # The folders worth cleaning, from the same walk (cluster_tools.cleanup).
    folders = cleanup.Folders([os.path.join(cfg["root"], login) for login in cfg["people"]]
                              + [os.path.join(cfg["projects"], d) for d in projects_of(host, cfg)])  # fmt: skip
    for path, (owner, nbytes, used, _) in files.items():
        folders.add(path, owner, nbytes, used)
    candidates = folders.ranked(now, cfg["stale_days"], cfg["cleanup_top"])
    due = plan(cfg, files, state, truncated, now)
    removed = remove(host, cfg, due, now, check, say)
    if truncated:
        say(f"the walk stopped at max_entries ({cfg['max_entries']}): files past it keep their dates")
    if check:
        return
    write_atomic(cfg["state"], json.dumps({"due": due}), mode=0o600)
    write_lists(host, cfg, due, now, say)
    write_textfile(
        cfg["textfile"],
        metrics(cfg, due, bursts, removed, scanned, truncated, now, candidates),
        cfg["textfile_kept"],
    )


# What a person sees.


def read_list(text):
    """(stale_days, [(due date, bytes, path)]) of a list file's TEXT."""
    stale, rows = None, []
    for line in text.splitlines():
        if line.startswith("#"):
            for word in line[1:].split():
                if word.startswith("stale_days="):
                    stale = int(word.split("=", 1)[1])
            continue
        parts = line.split("\t", 2)
        if len(parts) == 3:
            rows.append((parts[0], int(parts[1]), unescape(parts[2])))
    return stale, rows


def show(host, root, login, now, list_name=LIST_NAME):
    """What of LOGIN's is due to go, each file looked at again: (text, exit status)."""
    path = os.path.join(root, login, list_name)
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as f:
            stale, rows = read_list(f.read())
    except FileNotFoundError:
        return f"Nothing of yours on {root} is due for removal.\n", 0
    stale = stale or 60
    cutoff = now - stale * DAY
    out, due_n, due_b, spared = [], 0, 0, 0
    for date, nbytes, p in rows:
        try:
            st = host.lstat(p)
        except OSError:
            status = "gone"
        else:
            if last_use(st) >= cutoff:
                status = f"spared (used {time.strftime('%Y-%m-%d', time.localtime(last_use(st)))})"
                spared += 1
            else:
                status = f"goes {date}"
                due_n += 1
                due_b += nbytes
        out.append(f"{status:<26} {size(nbytes):>8}  {escape(p)}")
    head = (
        f"Files of yours on {root} unused for {stale} days: each goes on its date unless you use it"
        f" (read or write) before. {due_n} due ({size(due_b)}), {spared} spared since the list was written."
    )
    return "\n".join([head, "", f"{'STATUS':<26} {'SIZE':>8}  PATH", *out]) + "\n", 0


COMMAND = Command(
    "scratch-space",
    "which of your files on the scratch space are due to be cleaned up, and when",
    [
        Opt(
            "--root",
            metavar="DIR",
            brief=True,
            help="the scratch space (default: the cluster's, scratch_root in cluster.json)",
        )
    ],
    notes=[
        "Files nobody used (read or written) for the cluster's stale period are listed with\n"
        "the date they go; using one before then keeps it. The list is rewritten daily;\n"
        "this looks at each file again, so one you just used shows as spared. Run as root\n"
        "from a timer: scratch-space CONFIG.json purge [--check]."
    ],
    summary_ko="스크래치 공간에서 정리될 예정인 내 파일과 그 시점",
)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) in (2, 3) and argv[1] == "purge" and argv[2:] in ([], ["--check"]):

        def run():
            purge(Host(), load_config(argv[0]), time.time(), check=argv[2:] == ["--check"])
            return 0

        return run_main("scratch-space", run)
    args, _ = COMMAND.parse(argv)

    def body():
        root = args.root or site.info().get("scratch_root")
        if not root:
            raise SystemExit(
                "scratch-space: the cluster names no scratch space (scratch_root in "
                f"{site.PATH}); give --root DIR"
            )
        text, status = show(Host(), root, pwd.getpwuid(os.getuid()).pw_name, time.time())
        sys.stdout.write(text)
        return status

    return run_main("scratch-space", body)


if __name__ == "__main__":
    sys.exit(main())
