"""yaml.safe_load through libyaml: the one YAML loader every tool and test uses.

    load(text_or_stream)      -> the document, as yaml.safe_load gives it
    load_all(text_or_stream)  -> its documents, as yaml.safe_load_all gives them
    Loader                    -> the loader class, to subclass for a tag of one's own

PyYAML's own parser is pure Python, and parsing the repository's YAML was most of the
time of the tools and tests that read it (scripts/docs/site_facts.py: 6 s, then 1.5 s).
libyaml gives the same documents (every tracked YAML file compared, 2026-11-28) several
times faster; without it (a PyYAML built without libyaml) this is yaml.safe_load.
"""

import yaml

Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def load(stream):
    return yaml.load(stream, Loader=Loader)  # noqa: S506 - a safe loader


def load_all(stream):
    return yaml.load_all(stream, Loader=Loader)  # noqa: S506 - a safe loader
