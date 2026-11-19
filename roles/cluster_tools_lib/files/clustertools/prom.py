"""prom: the Prometheus text format node_exporter's textfile collector reads.

    key("slurm_node_gpus", node="gpu1")    -> a hashable series identity
    series(name, {labels}, value)          -> one line
    render({key: value})                   -> the lines, sorted, newline-terminated
    parse(text)                            -> {key: value}, the inverse of render

Label values are escaped (backslash, quote, newline) as the format requires. Python
3.9 or newer, standard library only.
"""

import re

_LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})? (\S+)$")
_PAIR = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')
_UNESCAPE = {"\\\\": "\\", '\\"': '"', "\\n": "\n"}


def _escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def key(name, **labels):
    return (name, tuple(sorted(labels.items())))


def series(name, labels, value):
    inner = ",".join(f'{k}="{_escape(v)}"' for k, v in labels.items())
    return f"{name}{{{inner}}} {value}"


def render(values):
    return "".join(
        series(name, dict(labels), value) + "\n" for (name, labels), value in sorted(values.items())
    )


def parse(text):
    values = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            labels = {
                k: re.sub(r"\\.", lambda e: _UNESCAPE.get(e.group(0), e.group(0)), v)
                for k, v in _PAIR.findall(m.group(2) or "")
            }
            values[key(m.group(1), **labels)] = m.group(3)
    return values
