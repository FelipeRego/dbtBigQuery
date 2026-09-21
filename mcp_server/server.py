"""
An MCP server that puts an LLM in front of a governed dbt semantic layer.

The point of this server is what it does *not* expose. There is no tool here
that accepts SQL. An agent connected to it cannot write `select count(*) from
fct_events`; it can only name metrics that exist, group them by dimensions the
semantic layer says are joinable, and filter on fields the layer knows about.
Everything else is refused before a query is ever built.

That constraint is the product. An agent writing raw SQL against a warehouse
will happily compute D7 retention three different ways in three conversations
and present all three with equal confidence. Here, the definition lives in
`models/semantic/metrics.yml`, MetricFlow compiles it, and every answer this
server returns carries the definition, the assumption behind it, the trade-off
that was accepted, and what would break if someone chose differently — read
straight out of the compiled manifest, not restated by the model.

Transport is stdio. Run it with:

    ga4-semantic-mcp

or point an MCP client at `.venv/bin/python -m mcp_server.server`.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

# --------------------------------------------------------------------------
# Locations and limits
# --------------------------------------------------------------------------

PROJECT_DIR = Path(
    os.environ.get("GA4_DBT_PROJECT_DIR", Path(__file__).resolve().parent.parent)
).resolve()

SEMANTIC_MANIFEST = PROJECT_DIR / "target" / "semantic_manifest.json"
DBT_MANIFEST = PROJECT_DIR / "target" / "manifest.json"

# A hard ceiling on rows returned to the model. A metric query that wants more
# than this is almost always a group-by mistake, and dumping 50,000 rows into a
# context window helps nobody.
MAX_ROWS = 500
DEFAULT_ROWS = 100
QUERY_TIMEOUT_SECONDS = 300

GOVERNANCE_FIELDS = ("definition", "assumption", "tradeoff", "breaks_if", "grain")


# --------------------------------------------------------------------------
# The semantic layer, read from dbt's compiled artefacts
# --------------------------------------------------------------------------


class SemanticLayerNotBuilt(RuntimeError):
    """Raised when dbt has not been parsed, so there is nothing to serve."""


class SemanticLayer:
    """
    A read-only view over the compiled semantic manifest.

    Nothing here queries the warehouse. This class answers "what exists and what
    does it mean"; `_run_mf` answers "what is the number".
    """

    def __init__(self) -> None:
        self._semantic: dict[str, Any] | None = None
        self._dbt: dict[str, Any] | None = None
        self._mtime: float | None = None

    # -- loading ----------------------------------------------------------

    def _load(self) -> None:
        if not SEMANTIC_MANIFEST.exists():
            raise SemanticLayerNotBuilt(
                f"No semantic manifest at {SEMANTIC_MANIFEST}. "
                "Run `dbt parse` in the project directory first."
            )
        mtime = SEMANTIC_MANIFEST.stat().st_mtime
        if self._semantic is not None and mtime == self._mtime:
            return
        self._semantic = json.loads(SEMANTIC_MANIFEST.read_text())
        self._dbt = json.loads(DBT_MANIFEST.read_text()) if DBT_MANIFEST.exists() else {}
        self._mtime = mtime

    @property
    def semantic(self) -> dict[str, Any]:
        self._load()
        assert self._semantic is not None
        return self._semantic

    # -- metrics ----------------------------------------------------------

    def metrics(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for metric in self.semantic.get("metrics", []):
            meta = (metric.get("config") or {}).get("meta") or {}
            out[metric["name"]] = {
                "name": metric["name"],
                "label": metric.get("label"),
                "type": metric.get("type"),
                "description": _clean(metric.get("description")),
                "filter": _render_filter(metric.get("filter")),
                "inputs": _metric_inputs(metric),
                "governance": {k: _clean(meta.get(k)) for k in GOVERNANCE_FIELDS if meta.get(k)},
            }
        return out

    def metric(self, name: str) -> dict[str, Any]:
        metrics = self.metrics()
        if name not in metrics:
            raise KeyError(
                f"Unknown metric '{name}'. This semantic layer defines "
                f"{len(metrics)} metrics; call list_metrics to see them. "
                "Metrics cannot be invented at query time — if the number you "
                "want is not here, it needs to be defined in "
                "models/semantic/metrics.yml and reviewed."
            )
        return metrics[name]

    # -- semantic models --------------------------------------------------

    def semantic_models(self) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        for sm in self.semantic.get("semantic_models", []):
            models.append(
                {
                    "name": sm["name"],
                    "description": _clean(sm.get("description")),
                    "table": (sm.get("node_relation") or {}).get("relation_name"),
                    "primary_entity": next(
                        (e["name"] for e in sm.get("entities", []) if e.get("type") == "primary"),
                        None,
                    ),
                    "entities": [
                        {"name": e["name"], "type": e.get("type")} for e in sm.get("entities", [])
                    ],
                    "dimensions": [
                        {
                            "name": d["name"],
                            "type": d.get("type"),
                            "description": _clean(d.get("description")),
                        }
                        for d in sm.get("dimensions", [])
                    ],
                    "measures": [
                        {
                            "name": m["name"],
                            "agg": m.get("agg"),
                            "description": _clean(m.get("description")),
                        }
                        for m in sm.get("measures", [])
                    ],
                }
            )
        return models


def _clean(text: str | None) -> str | None:
    """Collapse the whitespace that YAML folded scalars leave behind."""
    if not text:
        return None
    return " ".join(text.split())


def _render_filter(flt: Any) -> str | None:
    if not flt:
        return None
    if isinstance(flt, dict):
        parts = [w.get("where_sql_template") for w in flt.get("where_filters", [])]
        return " AND ".join(p for p in parts if p) or None
    return str(flt)


def _metric_inputs(metric: dict[str, Any]) -> dict[str, Any]:
    """Name the measures or metrics a metric is built from, so the chain is visible."""
    tp = metric.get("type_params") or {}
    inputs: dict[str, Any] = {}
    if tp.get("measure"):
        inputs["measure"] = tp["measure"].get("name")
    for side in ("numerator", "denominator"):
        if tp.get(side):
            entry: dict[str, Any] = {"name": tp[side].get("name")}
            if flt := _render_filter(tp[side].get("filter")):
                entry["filter"] = flt
            inputs[side] = entry
    if tp.get("metrics"):
        inputs["metrics"] = [m.get("name") for m in tp["metrics"]]
    return inputs


LAYER = SemanticLayer()


# --------------------------------------------------------------------------
# MetricFlow subprocess plumbing
# --------------------------------------------------------------------------


def _mf_executable() -> str:
    local = PROJECT_DIR / ".venv" / "bin" / "mf"
    if local.exists():
        return str(local)
    found = shutil.which("mf")
    if not found:
        raise RuntimeError(
            "MetricFlow CLI not found. Install the project with "
            "`uv pip install -e .` inside the repo's virtualenv."
        )
    return found


def _mf_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("DBT_PROFILES_DIR", str(PROJECT_DIR))
    return env


def _run_mf(args: list[str], timeout: int = QUERY_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_mf_executable(), *args],
        cwd=PROJECT_DIR,
        env=_mf_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
        # Deliberately not check=True. A failed MetricFlow query is an expected
        # outcome here — a bad group-by, a filter on a dimension with no join
        # path — and every caller turns the returncode into a structured error
        # the agent can learn from, which an exception would flatten.
        check=False,
    )


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _governance_for(metric_names: list[str]) -> dict[str, Any]:
    catalogue = LAYER.metrics()
    out: dict[str, Any] = {}
    for name in metric_names:
        if name in catalogue:
            entry = catalogue[name]
            out[name] = {
                "label": entry["label"],
                "type": entry["type"],
                **entry["governance"],
            }
    return out


def _refusal(unknown: list[str]) -> dict[str, Any]:
    """
    Build a structured refusal for metrics that do not exist.

    Returned rather than raised, so that the agent receives the reason and the
    valid alternatives instead of an opaque tool error. Refusing usefully is
    part of the governance story: the model should learn that the metric must
    be *defined*, not approximated.
    """
    return {
        "ok": False,
        "refused": True,
        "error": f"Unknown metric(s): {', '.join(unknown)}.",
        "reason": (
            "This server only serves metrics defined in the semantic layer. It "
            "has no raw-SQL tool, so a metric that is not defined cannot be "
            "approximated here."
        ),
        "next_step": (
            "Either pick an existing metric from available_metrics, or tell the "
            "user the metric would need to be added to "
            "models/semantic/metrics.yml and reviewed before it can be reported."
        ),
        "available_metrics": sorted(LAYER.metrics()),
    }


def _unknown_metrics(metric_names: list[str]) -> list[str]:
    catalogue = LAYER.metrics()
    return [m for m in metric_names if m not in catalogue]


# --------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------

INSTRUCTIONS = """
This server exposes a governed dbt/MetricFlow semantic layer built on the GA4
obfuscated ecommerce sample. It answers product-analytics questions about
activation, retention, feature adoption, session depth and the purchase funnel.

How to use it:

1. Call `list_metrics` to see what can be measured. Do not guess metric names.
2. Call `describe_metric` before reporting any number to a person. Every metric
   carries a definition, the assumption it rests on, the trade-off that was
   accepted, and what breaks if someone chooses differently. These are part of
   the answer, not footnotes.
3. Call `list_dimensions` to find out what a metric can legitimately be sliced
   by. Not every dimension is joinable to every metric.
4. Call `query_metrics` to get numbers.

There is deliberately no tool for running SQL. If a question cannot be answered
from the metrics defined here, the correct response is to say so and describe
what would need to be added to the semantic layer — not to approximate it.

When you report a number, state the definition alongside it. When a metric's
`breaks_if` note is relevant to how the user phrased their question, say so.
""".strip()

server = MCPServer(
    name="ga4-semantic-layer",
    title="GA4 governed semantic layer",
    instructions=INSTRUCTIONS,
)


@server.tool()
def list_metrics(search: str = "") -> dict[str, Any]:
    """
    List every metric the semantic layer defines.

    Args:
        search: Optional case-insensitive substring to filter metric names,
            labels and descriptions. Leave empty to list all.

    Returns the metric name, label, type, one-line description and the short
    definition. Use `describe_metric` for the full governance block.
    """
    catalogue = LAYER.metrics()
    needle = search.lower().strip()
    rows = []
    for name in sorted(catalogue):
        entry = catalogue[name]
        haystack = " ".join(
            str(x) for x in (name, entry["label"], entry["description"]) if x
        ).lower()
        if needle and needle not in haystack:
            continue
        rows.append(
            {
                "name": name,
                "label": entry["label"],
                "type": entry["type"],
                "grain": entry["governance"].get("grain"),
                "description": entry["description"],
            }
        )
    return {
        "count": len(rows),
        "metrics": rows,
        "note": (
            "Call describe_metric before reporting any of these numbers. "
            "This layer has no raw-SQL escape hatch by design."
        ),
    }


@server.tool()
def describe_metric(metric: str) -> dict[str, Any]:
    """
    Return the full governed definition of one metric.

    Args:
        metric: The exact metric name, as returned by `list_metrics`.

    Returns the definition, the assumption it rests on, the trade-off accepted
    when it was chosen, what breaks if someone defines it differently, its
    grain, and the measures or metrics it is computed from. This is the
    authoritative text — it is read from the compiled dbt manifest, which is
    generated from models/semantic/metrics.yml.
    """
    if unknown := _unknown_metrics([metric]):
        return _refusal(unknown)
    entry = LAYER.metric(metric)
    return {
        "name": entry["name"],
        "label": entry["label"],
        "type": entry["type"],
        "description": entry["description"],
        "computed_from": entry["inputs"],
        "built_in_filter": entry["filter"],
        "governance": entry["governance"],
        "source_of_truth": "models/semantic/metrics.yml",
    }


@server.tool()
def list_semantic_models() -> dict[str, Any]:
    """
    List the semantic models — the tables the metrics are built on, with their
    entities, groupable dimensions and aggregatable measures.

    Use this to understand why two metrics can or cannot be sliced the same way.
    """
    return {"semantic_models": LAYER.semantic_models()}


@server.tool()
def list_dimensions(metrics: str) -> dict[str, Any]:
    """
    List the dimensions a given set of metrics can legitimately be grouped by.

    Args:
        metrics: One or more metric names, comma-separated. When several are
            given, only dimensions valid for *all* of them are returned —
            which is the honest answer, because MetricFlow will refuse a
            group-by that has no join path to one of the metrics.

    Dimension names are returned in MetricFlow's `entity__dimension` form,
    which is exactly what `query_metrics` expects in `group_by`.
    """
    names = _split(metrics)
    if not names:
        raise ValueError("Pass at least one metric name.")
    if unknown := _unknown_metrics(names):
        return _refusal(unknown)

    result = _run_mf(["list", "dimensions", "--metrics", ",".join(names)], timeout=120)
    if result.returncode != 0:
        return {
            "metrics": names,
            "error": (result.stderr or result.stdout).strip()[:4000],
        }
    dims = [
        line.strip().lstrip("•").strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith("•")
    ]
    return {
        "metrics": names,
        "dimension_count": len(dims),
        "dimensions": dims,
        "note": (
            "metric_time is the metric's own aggregation time dimension. For "
            "cohort metrics such as activation and retention it is the user's "
            "first-seen date, not the date something happened."
        ),
    }


@server.tool()
def query_metrics(
    metrics: str,
    group_by: str = "",
    start_time: str = "",
    end_time: str = "",
    where: str = "",
    order_by: str = "",
    limit: int = DEFAULT_ROWS,
) -> dict[str, Any]:
    """
    Run a metric query through MetricFlow and return the rows, together with the
    governed definition of every metric involved.

    Args:
        metrics: Metric names, comma-separated. Must already exist in the layer.
        group_by: Dimensions to group by, comma-separated, in MetricFlow's
            `entity__dimension` form (e.g. `user__acquisition_medium`).
            Use `metric_time` for the metric's own time axis, optionally with a
            grain suffix such as `metric_time__week`.
        start_time: Inclusive ISO date lower bound on metric_time, e.g. 2021-01-01.
        end_time: Inclusive ISO date upper bound on metric_time.
        where: A MetricFlow filter expression, e.g.
            "{{ Dimension('user__first_device_category') }} = 'mobile'".
            Dimensions must be referenced through the Dimension() wrapper; raw
            column names are rejected by the layer, not by this server.
        order_by: Fields to sort by, comma-separated. Prefix with `-` for
            descending, e.g. `-metric_time`.
        limit: Maximum rows to return. Capped at 500.

    The response always includes a `governance` block. Report it alongside the
    numbers rather than presenting the figures bare.
    """
    metric_names = _split(metrics)
    if not metric_names:
        raise ValueError("Pass at least one metric name.")
    if unknown := _unknown_metrics(metric_names):
        return _refusal(unknown)

    limit = max(1, min(int(limit), MAX_ROWS))

    args = ["query", "--metrics", ",".join(metric_names), "--limit", str(limit)]
    if group_by:
        args += ["--group-by", ",".join(_split(group_by))]
    if start_time:
        args += ["--start-time", start_time]
    if end_time:
        args += ["--end-time", end_time]
    if where:
        args += ["--where", where]
    if order_by:
        args += ["--order", ",".join(_split(order_by))]

    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "result.csv"
        result = _run_mf(args + ["--csv", str(csv_path)])
        if result.returncode != 0:
            return {
                "ok": False,
                "metrics": metric_names,
                "error": (result.stderr or result.stdout).strip()[:4000],
                "hint": (
                    "If this is a group-by error, call list_dimensions for these "
                    "metrics — not every dimension has a join path to every metric."
                ),
                "governance": _governance_for(metric_names),
            }
        rows: list[dict[str, str]] = []
        if csv_path.exists():
            with csv_path.open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))

    return {
        "ok": True,
        "metrics": metric_names,
        "group_by": _split(group_by),
        "filters": {
            "where": where or None,
            "start_time": start_time or None,
            "end_time": end_time or None,
        },
        "row_count": len(rows),
        "truncated": len(rows) >= limit,
        "rows": rows,
        "governance": _governance_for(metric_names),
        "note": (
            "These numbers mean exactly what the governance block says they "
            "mean. If the user's question implies a different definition, say "
            "so rather than reinterpreting the figures."
        ),
    }


@server.tool()
def explain_metric_sql(
    metrics: str,
    group_by: str = "",
    start_time: str = "",
    end_time: str = "",
    where: str = "",
) -> dict[str, Any]:
    """
    Return the SQL MetricFlow would run for a query, without running it.

    Args:
        metrics: Metric names, comma-separated.
        group_by: Dimensions to group by, comma-separated.
        start_time: Inclusive ISO date lower bound on metric_time.
        end_time: Inclusive ISO date upper bound on metric_time.
        where: A MetricFlow filter expression.

    Useful for showing a person how a metric is actually computed, and for
    checking a query's cost before spending warehouse credits on it.
    """
    metric_names = _split(metrics)
    if not metric_names:
        raise ValueError("Pass at least one metric name.")
    if unknown := _unknown_metrics(metric_names):
        return _refusal(unknown)

    args = ["query", "--metrics", ",".join(metric_names), "--explain"]
    if group_by:
        args += ["--group-by", ",".join(_split(group_by))]
    if start_time:
        args += ["--start-time", start_time]
    if end_time:
        args += ["--end-time", end_time]
    if where:
        args += ["--where", where]

    result = _run_mf(args)
    if result.returncode != 0:
        return {
            "ok": False,
            "metrics": metric_names,
            "error": (result.stderr or result.stdout).strip()[:4000],
        }
    return {
        "ok": True,
        "metrics": metric_names,
        "sql": result.stdout.strip()[:20000],
        "governance": _governance_for(metric_names),
    }


@server.tool()
def health_check() -> dict[str, Any]:
    """
    Report whether the semantic layer is ready to serve queries: manifests
    present, MetricFlow available, warehouse credentials configured.

    Call this first if a query fails for reasons that look environmental.
    """
    checks: dict[str, Any] = {
        "project_dir": str(PROJECT_DIR),
        "semantic_manifest_present": SEMANTIC_MANIFEST.exists(),
        "dbt_manifest_present": DBT_MANIFEST.exists(),
        "bigquery_project_env_set": bool(os.environ.get("DBT_BIGQUERY_PROJECT")),
    }
    try:
        checks["metricflow_executable"] = _mf_executable()
    except RuntimeError as exc:
        checks["metricflow_executable"] = None
        checks["metricflow_error"] = str(exc)
    try:
        catalogue = LAYER.metrics()
        checks["metric_count"] = len(catalogue)
        checks["semantic_model_count"] = len(LAYER.semantic_models())
    except SemanticLayerNotBuilt as exc:
        checks["metric_count"] = 0
        checks["error"] = str(exc)
    checks["ready"] = bool(
        checks.get("semantic_manifest_present")
        and checks.get("metric_count")
        and checks.get("metricflow_executable")
    )
    return checks


def main() -> None:
    """Console-script entry point. Serves over stdio."""
    server.run()


if __name__ == "__main__":
    main()
