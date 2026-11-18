"""Building blocks for the Grafana dashboards deepops ships.

Every dashboard is written in Python from these helpers, so panel styling, layout,
units, colours and the query conventions live in one place:
  - each query deduplicates on the dimension it draws (max, max by (...)) before it
    sums or averages, so a changed label on a scrape target never splits a line or
    doubles a total;
  - gaps stay gaps (no "null as zero"); a panel says in words when there is nothing
    to show;
  - state colours mean the same thing on every dashboard.
"""

import json

DATASOURCE = {"type": "prometheus", "uid": "${datasource}"}
WIDTH = 24

# One colour per Slurm node state, on every dashboard.
STATE_COLORS = {
    "allocated": "blue",
    "mixed": "purple",
    "idle": "green",
    "completing": "light-blue",
    "draining": "yellow",
    "drained": "orange",
    "maintenance": "light-orange",
    "down": "red",
    "failed": "dark-red",
    "error": "dark-red",
}


def query(expr, legend="", *, instant=False, table=False, ref=None):
    target = {"expr": expr, "legendFormat": legend, "datasource": DATASOURCE}
    if instant:
        target["instant"] = True
        target["range"] = False
    if table:
        target["format"] = "table"
    if ref:
        target["refId"] = ref
    return target


def _targets(queries):
    out = []
    for i, q in enumerate(queries):
        q = dict(q)
        q.setdefault("refId", chr(ord("A") + i))
        out.append(q)
    return out


def _color_overrides(colors):
    return [
        {
            "matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}],
        }
        for name, color in colors.items()
    ]


def _panel(kind, title, w, h, queries, *, description="", defaults=None, overrides=(), options=None, **extra):
    panel = {
        "type": kind,
        "title": title,
        "description": description,
        "datasource": DATASOURCE,
        "w": w,
        "h": h,
        "fieldConfig": {"defaults": defaults or {}, "overrides": list(overrides)},
        "options": options or {},
        "targets": _targets(queries),
    }
    panel.update(extra)
    return panel


def timeseries(
    title,
    queries,
    *,
    unit="none",
    w=12,
    h=8,
    description="",
    stack=False,
    min=0,
    max=None,
    decimals=None,
    legend="list",
    calcs=("lastNotNull",),
    negative=None,
    colors=None,
    no_value=None,
    fill=None,
):
    """Lines (or a stack) over time. `negative` names a series, or /regex/, drawn below the axis."""
    custom = {
        "drawStyle": "line",
        "lineWidth": 1,
        "fillOpacity": fill if fill is not None else (60 if stack else 10),
        "spanNulls": False,
        "showPoints": "never",
        "stacking": {"mode": "normal" if stack else "none", "group": "A"},
    }
    defaults = {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"}}
    if min is not None and negative is None:
        defaults["min"] = min
    if max is not None:
        defaults["max"] = max
    if decimals is not None:
        defaults["decimals"] = decimals
    if no_value:
        defaults["noValue"] = no_value
    overrides = _color_overrides(colors or {})
    if negative:
        matcher = (
            {"id": "byRegexp", "options": negative}  # with the slashes: Grafana matches a bare pattern whole
            if negative.startswith("/")
            else {"id": "byName", "options": negative}
        )
        overrides.append(
            {"matcher": matcher, "properties": [{"id": "custom.transform", "value": "negative-Y"}]}
        )
    table = legend == "table"
    options = {
        "legend": {
            "showLegend": True,
            "displayMode": legend,
            "placement": "right" if table else "bottom",
            "calcs": list(calcs),
        },
        "tooltip": {"mode": "multi", "sort": "desc"},
    }
    if table:
        options["legend"].update({"sortBy": "Last *", "sortDesc": True})
    return _panel(
        "timeseries",
        title,
        w,
        h,
        queries,
        description=description,
        defaults=defaults,
        overrides=overrides,
        options=options,
    )


def stat(
    title,
    expr,
    *,
    unit="none",
    w=4,
    h=4,
    description="",
    decimals=None,
    thresholds=None,
    no_value=None,
    graph=False,
    field=None,
    value_size=None,
):
    """One current number. Thresholds are [(value, colour), ...] from the lowest; none = plain text.
    `field` shows that label of the series instead of its value (a hostname, a kernel)."""
    steps = [{"color": "text", "value": None}]
    if thresholds:
        steps = [{"color": thresholds[0][1], "value": None}] + [
            {"color": color, "value": value} for value, color in thresholds[1:]
        ]
    defaults = {
        "unit": unit,
        "color": {"mode": "thresholds"},
        "thresholds": {"mode": "absolute", "steps": steps},
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    if no_value:
        defaults["noValue"] = no_value
    options = {
        "reduceOptions": {
            "calcs": ["lastNotNull"],
            "fields": f"/^{field}$/" if field else "",
            "values": False,
        },
        "colorMode": "none" if field else "value",
        "graphMode": "area" if graph else "none",
        "textMode": "value",
        "justifyMode": "center",
        "wideLayout": True,
    }
    if value_size:
        options["text"] = {"valueSize": value_size}
    return _panel(
        "stat",
        title,
        w,
        h,
        [query(expr, table=bool(field))],
        description=description,
        defaults=defaults,
        options=options,
    )


def bars(title, expr, by, *, unit="percentunit", w=12, h=8, description="", max=1, decimals=0, no_value=None):
    """One vertical bar per value of label `by`, sorted by it (an instant table query)."""
    defaults = {
        "unit": unit,
        "min": 0,
        "decimals": decimals,
        "displayName": "${__data.fields." + by + "}",
        "color": {"mode": "continuous-GrYlRd"},
    }
    if max is not None:
        defaults["max"] = max
    if no_value:
        defaults["noValue"] = no_value
    options = {
        "orientation": "vertical",
        "displayMode": "basic",
        "showUnfilled": True,
        "valueMode": "color",
        "namePlacement": "auto",
        "sizing": "auto",
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
    }
    return _panel(
        "bargauge",
        title,
        w,
        h,
        [query(expr, instant=True, table=True)],
        description=description,
        defaults=defaults,
        options=options,
        transformations=[{"id": "sortBy", "options": {"sort": [{"field": by}]}}],
    )


def table(
    title, expr, columns, *, w=24, h=8, description="", sort=None, value=None, no_value="None", colors=None
):
    """An instant table with exactly `columns`, in that order. `value` = (name, unit, colour scheme)
    names and formats the query's value column; `colors` = {column: {regex: colour}} colours
    text cells by what they say."""
    overrides = []
    for column, rules in (colors or {}).items():
        mappings = [
            {"type": "regex", "options": {"pattern": pattern, "result": {"color": color, "index": i}}}
            for i, (pattern, color) in enumerate(rules.items())
        ]
        overrides.append(
            {
                "matcher": {"id": "byName", "options": column},
                "properties": [
                    {"id": "mappings", "value": mappings},
                    {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                ],
            }
        )
    rename = {}
    if value:
        name, unit, scheme = value
        rename["Value"] = name
        props = [{"id": "unit", "value": unit}]
        if scheme:
            props += [
                {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                {"id": "color", "value": {"mode": scheme}},
            ]
        overrides.append({"matcher": {"id": "byName", "options": name}, "properties": props})
    transformations = [
        {
            "id": "organize",
            "options": {"renameByName": rename, "indexByName": {c: i for i, c in enumerate(columns)}},
        },
        {"id": "filterFieldsByName", "options": {"include": {"names": columns}}},
    ]
    if sort:
        transformations.append({"id": "sortBy", "options": {"sort": [{"field": sort}]}})
    return _panel(
        "table",
        title,
        w,
        h,
        [query(expr, instant=True, table=True)],
        description=description,
        defaults={"noValue": no_value, "custom": {"align": "auto"}},
        overrides=overrides,
        options={"showHeader": True, "cellHeight": "sm"},
        transformations=transformations,
    )


def text(title, content, *, w=24, h=3):
    panel = _panel("text", title, w, h, [], options={"mode": "markdown", "content": content})
    panel.pop("datasource")
    panel.pop("targets")
    return panel


def row(title, repeat=None):
    panel = {"type": "row", "title": title, "w": WIDTH, "h": 1, "collapsed": False, "panels": []}
    if repeat:
        panel["repeat"] = repeat
    return panel


def at(x, y, panel):
    """Pin a panel to a grid position instead of the flow (for dense hand-laid grids)."""
    return {**panel, "pos": (x, y)}


def layout(panels):
    """Place panels left to right, wrapping at the grid width (or where `at` pins them);
    give each an id."""
    x = y = line_h = 0
    out = []
    for pid, panel in enumerate(panels, start=1):
        panel = dict(panel)
        w, h = panel.pop("w"), panel.pop("h")
        if "pos" in panel:
            px, py = panel.pop("pos")
            panel["gridPos"] = {"x": px, "y": py, "w": w, "h": h}
            panel["id"] = pid
            out.append(panel)
            continue
        if x + w > WIDTH or panel["type"] == "row":
            x, y, line_h = 0, y + line_h, 0
        panel["gridPos"] = {"x": x, "y": y, "w": w, "h": h}
        panel["id"] = pid
        out.append(panel)
        x += w
        line_h = max(line_h, h)
        if panel["type"] == "row":
            x, y, line_h = 0, y + h, 0
    return out


def datasource_variable():
    return {
        "name": "datasource",
        "label": "Data source",
        "type": "datasource",
        "query": "prometheus",
        "current": {},
        "hide": 0,
        "refresh": 1,
    }


def query_variable(name, expr, regex, *, label=None, hide=False):
    """A variable filled from query_result(expr), reduced by a /regex/ capture."""
    return {
        "name": name,
        "label": label,
        "type": "query",
        "datasource": "$datasource",
        "query": expr,
        "regex": regex,
        "refresh": 1,
        "hide": 2 if hide else 0,
        "includeAll": False,
        "multi": False,
        "options": [],
        "current": {},
        "skipUrlSync": hide,
    }


def dashboard(uid, title, panels, *, tags, description, variables=(), refresh="1m", time_from="now-24h"):
    return {
        "uid": uid,
        "title": title,
        "id": None,
        "description": description,
        "tags": list(tags),
        "editable": False,
        "graphTooltip": 1,
        "refresh": refresh,
        "schemaVersion": 41,
        "time": {"from": time_from, "to": "now"},
        "timezone": "browser",
        "templating": {"list": [datasource_variable(), *variables]},
        "annotations": {"list": []},
        "links": [],
        "panels": layout(panels),
    }


def render(dash):
    return json.dumps(dash, indent=2, ensure_ascii=False) + "\n"
