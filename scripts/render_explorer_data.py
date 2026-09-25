#!/usr/bin/env python
"""
Regenerate the interactive explorer's data from the semantic layer YAML.

`docs/explorer/index.html` is a single self-contained page, published to GitHub
Pages, that lets a visitor browse the metrics, follow a question through the
MCP server, and see which dimensions each semantic model allows. Its data is a
JSON block embedded in the page. This script rewrites that block from
`models/semantic/metrics.yml` and `models/semantic/semantic_models.yml`, so the
explorer says exactly what the warehouse, the README and the agent say.

    python scripts/render_explorer_data.py            # rewrite the embedded data
    python scripts/render_explorer_data.py --check    # exit 1 if it is stale (CI)

The section a metric is filed under is the one editorial decision here; it is
inferred from the metric name so a new metric lands somewhere sensible without
anyone touching this file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent
METRICS_YAML = PROJECT_DIR / "models" / "semantic" / "metrics.yml"
SEMANTIC_YAML = PROJECT_DIR / "models" / "semantic" / "semantic_models.yml"
EXPLORER = PROJECT_DIR / "docs" / "explorer" / "index.html"

EMBED = re.compile(
    r'(<script id="embedded-data" type="application/json">)(.*?)(</script>)',
    re.DOTALL,
)


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _ref_name(value: Any) -> str:
    """A measure or metric reference is either a bare name or {name, filter}."""
    if isinstance(value, dict):
        return value.get("name", "")
    return str(value or "")


def section_for(name: str) -> str:
    if "activat" in name or name == "avg_days_to_first_purchase":
        return "Activation"
    if "retention" in name or name.startswith(("d1_", "d7_", "d30_")):
        return "Retention"
    if "feature" in name or name == "adoption_base_users":
        return "Feature adoption"
    if any(k in name for k in ("funnel", "checkout", "conversion", "reaching_")):
        return "Funnel"
    if name in {"new_users", "sessions", "revenue", "transactions"}:
        return "Volume"
    return "Engagement and session depth"


def load_semantic_models() -> list[dict]:
    doc = yaml.safe_load(SEMANTIC_YAML.read_text())
    out = []
    for model in doc.get("semantic_models", []):
        ref = re.search(r"ref\(['\"](\w+)['\"]\)", model.get("model", ""))
        out.append(
            {
                "name": model["name"],
                "source_model": ref.group(1) if ref else model.get("model", ""),
                "entities": [
                    {"name": e["name"], "type": e["type"], "expr": e.get("expr", e["name"])}
                    for e in model.get("entities", [])
                ],
                "dimensions": [d["name"] for d in model.get("dimensions", [])],
                "measures": [m["name"] for m in model.get("measures", [])],
            }
        )
    return out


def load_metrics(models: list[dict]) -> list[dict]:
    raw = yaml.safe_load(METRICS_YAML.read_text()).get("metrics", [])
    by_name = {m["name"]: m for m in raw}
    measure_owner = {measure: m["name"] for m in models for measure in m["measures"]}

    def refs(metric: dict) -> list[str]:
        tp = metric.get("type_params") or {}
        names = [_ref_name(tp.get(k)) for k in ("measure", "numerator", "denominator")]
        return [n for n in names if n]

    def owners(name: str, seen: frozenset[str] = frozenset()) -> set[str]:
        if name in measure_owner:
            return {measure_owner[name]}
        if name in seen or name not in by_name:
            return set()
        found: set[str] = set()
        for ref in refs(by_name[name]):
            found |= owners(ref, seen | {name})
        return found

    out = []
    for m in raw:
        meta = (m.get("config") or {}).get("meta") or {}
        tp = m.get("type_params") or {}
        item: dict[str, Any] = {
            "name": m["name"],
            "section": section_for(m["name"]),
            "label": m.get("label") or m["name"],
            "type": m.get("type", ""),
            "grain": _clean(meta.get("grain")),
        }
        for key in ("measure", "numerator", "denominator"):
            if tp.get(key):
                item[key] = _ref_name(tp[key])
        for key in ("description", "definition", "assumption", "tradeoff", "breaks_if"):
            value = _clean(m.get(key) if key == "description" else meta.get(key))
            if value:
                item[key] = value
        item["semantic_models"] = sorted(owners(m["name"]))
        out.append(item)
    return out


def render() -> str:
    models = load_semantic_models()
    payload = {
        "generated_from": [
            "models/semantic/metrics.yml",
            "models/semantic/semantic_models.yml",
        ],
        "metrics": load_metrics(models),
        "semantic_models": models,
    }
    # "</" would close the <script> tag early if a definition ever contained it.
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the explorer's embedded data is out of date, without rewriting it.",
    )
    args = parser.parse_args()

    current = EXPLORER.read_text()
    if not EMBED.search(current):
        sys.exit(f"{EXPLORER} is missing its embedded-data <script> block.")
    data = render()
    updated = EMBED.sub(lambda m: m.group(1) + data + m.group(3), current, count=1)
    n_metrics = len(json.loads(data.replace("<\\/", "</"))["metrics"])

    if args.check:
        if updated != current:
            print(
                "Explorer data is out of date.\nRun: python scripts/render_explorer_data.py",
                file=sys.stderr,
            )
            return 1
        print(f"Explorer data is current ({n_metrics} metrics).")
        return 0

    EXPLORER.write_text(updated)
    print(f"Wrote {n_metrics} metrics into {EXPLORER.relative_to(PROJECT_DIR)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
