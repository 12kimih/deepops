#!/usr/bin/env python3
"""Which pinned versions have a newer upstream release.

    upstream.py          report every pin and its newest upstream release
    upstream.py --check  exit 1 when any pin is behind (or its upstream cannot be read)
    upstream.py --json   the same as JSON

Every version deepops installs is pinned in a role's defaults, and the line above each
pin says where its releases are published, so the pin and its source sit together:

    # upstream: git https://github.com/NVIDIA/enroot v?(\\d+)\\.(\\d+)\\.(\\d+)
    enroot_version: "4.2.1"

  git URL REGEX      the repository's tags (git ls-remote, no API limits); REGEX matches
                     a release tag whole, its groups are the version's numbers
  pypi NAME [PY]     PyPI's newest release of NAME; with PY (3.9), the newest final
                     release whose Requires-Python admits that interpreter
  helm INDEX CHART   the newest non-prerelease version of CHART in a chart index.yaml
  deb PACKAGES NAME  the newest version of NAME in an APT Packages index (.gz or plain)
  oci REF REGEX      the tags of an OCI artifact (a Helm chart in a registry) matching
                     REGEX, as for git
  cuda               NVIDIA's CUDA listings (scripts/deepops/cuda_releases.py)
  manual URL         checked by a person (no machine-readable release list); listed
  hold REASON        deliberately not moved (REASON says why and until when); listed

Container images need no line: every repo:tag@sha256 pin (scripts/deepops/image_pins.py)
is compared with the newest tag of the same shape in its registry (v3.15.0 with vN.N.N,
4.6.1-4.8.4-distroless with N.N.N-N.N.N-distroless). A line above an image pin narrows
it (`# upstream: registry REGEX`, e.g. Ubuntu's LTS releases only) or holds it
(`# upstream: hold REASON`). Python's pins are pyproject.toml's.
Standard library only.
"""

import gzip
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cuda_releases  # noqa: E402
import image_pins  # noqa: E402
from repo_files import tracked  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ANNOTATION = re.compile(r"^\s*#\s*upstream:\s*(\S+)\s*(.*)$")
TIMEOUT = 60


def numbers(version):
    """The comparable form of a version: its numbers, in order (v4.2.1-1 -> (4, 2, 1, 1))."""
    return tuple(int(n) for n in re.findall(r"\d+", str(version)))


def is_behind(current, latest):
    """LATEST is newer than CURRENT, compared over CURRENT's precision: a pin of 29.10.0
    is current for the package 29.10.0-1~ubuntu.24.04~noble."""
    have = numbers(current)
    return numbers(latest)[: len(have)] > have


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "deepops-upstream"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        body = response.read()
    return gzip.decompress(body) if url.endswith(".gz") else body


def newest_git(url, pattern):
    out = subprocess.run(
        ["git", "ls-remote", "--tags", "--refs", url],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=TIMEOUT,
        check=True,
    ).stdout
    return newest_tag([line.split("refs/tags/", 1)[-1] for line in out.splitlines()], pattern)


def newest_tag(names, pattern):
    """The newest of NAMES matching PATTERN whole; its groups are the version's numbers."""
    tag = re.compile(pattern)
    found = [".".join(m.groups()) if m.groups() else m.group(0) for m in map(tag.fullmatch, names) if m]
    return max(found, key=numbers) if found else None


def newest_pypi(name, python=None):
    data = json.loads(fetch(f"https://pypi.org/pypi/{name}/json"))
    if python is None:
        return data["info"]["version"]
    found = [
        version
        for version, files in data["releases"].items()
        if re.fullmatch(r"\d+(\.\d+)*", version)
        and files
        and not all(f.get("yanked") for f in files)
        and all(admits(f.get("requires_python"), python) for f in files if not f.get("yanked"))
    ]
    return max(found, key=numbers) if found else None


def admits(spec, python):
    """Whether a Requires-Python SPEC (">=3.9, !=3.10.*") admits the interpreter PYTHON
    ("3.9"): the comparisons Requires-Python uses, over release numbers."""
    have = numbers(python)
    for clause in filter(None, (c.strip() for c in (spec or "").split(","))):
        op, version = re.fullmatch(r"(~=|==|!=|<=|>=|<|>)\s*(\S+)", clause).groups()
        if version.endswith(".*"):
            want = numbers(version[:-2])
            same = have[: len(want)] == want
            if (op == "==") != same:
                return False
            continue
        want = numbers(version)
        size = max(len(want), len(have))
        a, b = have + (0,) * (size - len(have)), want + (0,) * (size - len(want))
        ok = {
            "==": a == b,
            "!=": a != b,
            "<=": a <= b,
            ">=": a >= b,
            "<": a < b,
            ">": a > b,
            "~=": a >= b and have[: len(want) - 1] == want[:-1],
        }[op]
        if not ok:
            return False
    return True


def newest_helm(index, chart):
    entries = yaml.safe_load(fetch(index.rstrip("/") + "/index.yaml"))["entries"].get(chart, [])
    stable = [e["version"] for e in entries if not re.search(r"-(rc|alpha|beta|pre)", e["version"], re.I)]
    return max(stable, key=numbers) if stable else None


def newest_deb(packages, name):
    text = fetch(packages).decode(errors="replace")
    versions = re.findall(rf"^Package: {re.escape(name)}\n(?:[^\n]+\n)*?Version: (\S+)", text, re.M)
    versions = [v.split(":", 1)[-1] for v in versions]  # the epoch (5:29.10.0-1~...) aside
    return max(versions, key=numbers) if versions else None


def newest_cuda():
    found = cuda_releases.newest_per_minor(
        cuda_releases.releases_in(cuda_releases.fetch(f"{cuda_releases.BASE}/redist/"))
    )
    return ".".join(map(str, max(found)))


def current_cuda():
    block = cuda_releases.current_block(cuda_releases.DEFAULTS.read_text())
    return max(re.findall(r"\d+\.\d+\.\d+", block), key=numbers)


def newest(kind, args):
    if kind == "git":
        return newest_git(*args.split(None, 1))
    if kind == "pypi":
        return newest_pypi(*args.split())
    if kind == "helm":
        return newest_helm(*args.split())
    if kind == "deb":
        return newest_deb(*args.split())
    if kind == "cuda":
        return newest_cuda()
    if kind == "oci":
        ref, pattern = args.split(None, 1)
        return newest_tag(image_pins.tags(ref.removeprefix("oci://") + ":latest"), pattern)
    raise ValueError(f"unknown upstream kind {kind!r}")


def annotated_pins():
    """[(variable, value, kind, args, file:line)] for every `# upstream:` line in the
    roles' defaults and vars, with the pin on the line below it."""
    found = []
    for path in tracked("roles/*/defaults/main.yml", "roles/*/vars/*.yml", "roles/*/vars/*.yaml"):
        rel = path.relative_to(ROOT).as_posix()
        lines = (ROOT / rel).read_text().splitlines()
        for number, line in enumerate(lines, 1):
            m = ANNOTATION.match(line)
            if not m or m.group(1) == "registry":
                continue  # a registry line narrows the image pin below it: the image pass reads it
            pin = re.match(
                r"^([a-z_][a-z0-9_]*):\s*\"?([^\"#]*?)\"?\s*(#.*)?$",
                lines[number] if number < len(lines) else "",
            )
            if not pin:
                raise ValueError(f"{rel}:{number}: an upstream line names the pin on the line below it")
            found.append((pin.group(1), pin.group(2), m.group(1), m.group(2).strip(), f"{rel}:{number + 1}"))
    return found


def annotation_above(where):
    """(kind, args) of an `# upstream:` line right above FILE:LINE, or None."""
    rel, number = where.rsplit(":", 1)
    lines = (ROOT / rel).read_text().splitlines()
    m = ANNOTATION.match(lines[int(number) - 2]) if int(number) > 1 else None
    return (m.group(1), m.group(2).strip()) if m else None


def python_pins():
    text = (ROOT / "pyproject.toml").read_text()
    return [
        (name, version, "pypi", name, "pyproject.toml")
        for name, version in re.findall(r'"([\w.-]+)==([\w.]+)"', text)
    ]


def image_shape(tag):
    """A tag's shape: its numbers as \\d+, the rest literal (v3.15.0 -> v\\d+\\.\\d+\\.\\d+)."""
    return re.compile(
        "".join(r"\d+" if part.isdigit() else re.escape(part) for part in re.split(r"(\d+)", tag))
    )


def report():
    rows = []
    for name, value, kind, args, where in annotated_pins() + python_pins():
        row = {"pin": name, "current": value, "where": where, "source": f"{kind} {args}".strip()}
        if kind in ("manual", "hold"):
            row["status"] = "held" if kind == "hold" else "manual"
        else:
            try:
                latest = newest(kind, args)
                current = current_cuda() if kind == "cuda" else value
                row.update(current=current, latest=latest)
                row["status"] = (
                    "unknown" if latest is None else "behind" if is_behind(current, latest) else "current"
                )
            except Exception as e:  # an unreadable upstream is a finding, not a crash
                row.update(status="unknown", error=f"{type(e).__name__}: {e}"[:200])
        rows.append(row)
    seen = {r["where"] for r in rows}  # an image pin with its own upstream line is reported once
    for ref, _sha, where in image_pins.pins():
        image, tag = ref.rsplit(":", 1)
        if image in seen or where in seen:
            continue
        seen.add(image)
        row = {"pin": image, "current": tag, "where": where, "source": "registry"}
        kind, args = annotation_above(where) or ("registry", "")
        if kind == "hold":
            rows.append({**row, "status": "held", "source": f"hold {args}"})
            continue
        try:
            shape = re.compile(args) if kind == "registry" and args else image_shape(tag)
            same = [t for t in image_pins.tags(ref) if shape.fullmatch(t)]
            latest = max(same, key=numbers) if same else None
            row.update(
                latest=latest,
                status="unknown" if latest is None else "behind" if is_behind(tag, latest) else "current",
            )
        except Exception as e:
            row.update(status="unknown", error=f"{type(e).__name__}: {e}"[:200])
        rows.append(row)
    return rows


def main(argv):
    rows = report()
    if "--json" in argv:
        print(json.dumps(rows, indent=2))
    else:
        for r in sorted(rows, key=lambda r: (r["status"] != "behind", r["status"] != "unknown", r["pin"])):
            latest = f" -> {r['latest']}" if r.get("latest") and r["status"] == "behind" else ""
            extra = (
                f"  ({r['error']})"
                if r.get("error")
                else f"  [{r['source']}]"
                if r["status"] in ("manual", "held")
                else ""
            )
            print(f"{r['status']:8} {r['pin']} {r['current']}{latest}  {r['where']}{extra}")
    bad = [r for r in rows if r["status"] in ("behind", "unknown")]
    return 1 if "--check" in argv and bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
